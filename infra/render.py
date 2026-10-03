"""Turn infra/config/cluster.yaml into manifests and the gateway's environment.

One config in; per-host Kubernetes manifests out, plus the URL lists the gateway
reads. Model, topology and engine flags appear exactly once. Hand-edited
manifests make "same cluster, only the policy changed" a claim nobody can check.

    python -m infra.render infra/config/cluster.yaml                 # manifests
    python -m infra.render infra/config/cluster.yaml --host gpu-a
    python -m infra.render infra/config/cluster.yaml --env           # gateway env
    python -m infra.render infra/config/cluster.yaml --plan          # what lands where

Static manifests (gateway, mooncake, open-webui, observability) are not
generated — they do not vary with the model.

Hosts are INDEPENDENT single-node clusters. The gateway federates by URL list,
so a second box is a second set of URLs rather than a second control plane. A
tensor-parallel group therefore cannot span hosts, which is the correct
restriction anyway: TP all-reduces every layer and expects NVLink.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VLLM_IMAGE = "vllm/vllm-openai:v0.11.0"
BASE_HOST_PORT = 8000
BASE_NODE_PORT = 30800

# Pool names are fixed by the gateway's env contract, not by preference:
# router/pools.py reads PREFILL_URLS and DECODE_URLS and nothing else.
POOLS_FOR_MODE = {
    "aggregated": ("engine",),
    "disaggregated": ("prefill", "decode"),
}

# Bytes per parameter. Used only to refuse a model that cannot fit — an
# estimate is enough to catch the error that costs a weights download.
BYTES_PER_PARAM = {"fp8": 1.0, "int8": 1.0, "awq": 0.5, "gptq": 0.5}
DEFAULT_BYTES_PER_PARAM = 2.0  # bf16 / fp16 / "auto"

# Weights must leave this fraction of usable HBM free, per card, or the config
# is refused. Fitting is not the bar — fitting with room to serve is.
MIN_KV_HEADROOM_FRACTION = 0.05


class ConfigError(ValueError):
    """The config cannot produce a cluster. Raised before anything is rented."""


@dataclass(frozen=True)
class Host:
    name: str
    ssh: str
    ssh_key: str
    gpu_kind: str
    gpu_count: int
    hbm_gb: float


@dataclass(frozen=True)
class Worker:
    pool: str
    replica: int
    index: int  # position on its own host, which is what ports key off
    host: Host
    model_id: str
    served_name: str
    capability: str
    quantization: str | None
    tp: int
    pp: int

    @property
    def name(self) -> str:
        return f"vllm-{self.pool}-{self.replica}"

    @property
    def host_port(self) -> int:
        return BASE_HOST_PORT + self.index

    @property
    def node_port(self) -> int:
        return BASE_NODE_PORT + self.index

    @property
    def gpus(self) -> int:
        return self.tp * self.pp


def load(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a mapping")
    return raw


def hosts_from(cfg: dict[str, Any]) -> dict[str, Host]:
    raw = cfg.get("hosts") or []
    if not raw:
        raise ConfigError("no hosts declared. At least one is required.")
    out: dict[str, Host] = {}
    for h in raw:
        name = str(h["name"])
        if name in out:
            raise ConfigError(f"duplicate host name {name!r}")
        gpu = h.get("gpu") or {}
        out[name] = Host(
            name=name,
            ssh=str(h.get("ssh", "")),
            ssh_key=str(h.get("ssh_key", "")),
            gpu_kind=str(gpu.get("kind", "unknown")),
            gpu_count=int(gpu.get("count", 0)),
            hbm_gb=float(gpu.get("hbm_gb", 0)),
        )
    return out


def workers_from(cfg: dict[str, Any]) -> list[Worker]:
    """Expand pools into one Worker per replica, numbered per host."""
    hosts = hosts_from(cfg)
    topo = cfg["topology"]
    model = cfg["model"]
    pools = topo.get("pools") or {}
    per_host_index: dict[str, int] = dict.fromkeys(hosts, 0)
    out: list[Worker] = []

    # Iterate in the mode's declared pool order so ports and URL lists are
    # stable across renders. Dict order would depend on how the YAML was typed.
    for pool_name in POOLS_FOR_MODE.get(str(topo.get("mode", "")), ()):
        spec = pools.get(pool_name)
        if spec is None:
            continue
        host_name = str(spec.get("host", ""))
        if host_name not in hosts:
            raise ConfigError(
                f"pool {pool_name!r} names host {host_name!r}, which is not declared. "
                f"Known hosts: {', '.join(sorted(hosts)) or 'none'}"
            )
        host = hosts[host_name]
        m = spec.get("model") or {}
        for replica in range(int(spec.get("replicas", 1))):
            out.append(
                Worker(
                    pool=pool_name,
                    replica=replica,
                    index=per_host_index[host_name],
                    host=host,
                    model_id=m.get("id", model["id"]),
                    served_name=m.get("served_name", model.get("served_name", "lab")),
                    capability=m.get("capability", model.get("capability", "text")),
                    quantization=m.get("quantization", model.get("quantization")),
                    tp=int(spec.get("tensor_parallel_size", 1)),
                    pp=int(spec.get("pipeline_parallel_size", 1)),
                )
            )
            per_host_index[host_name] += 1
    return out


def weights_gb(model_id: str, quantization: str | None) -> float | None:
    """Rough weight footprint, or None when the parameter count is not in the name.

    Only used to refuse an impossible fit. Being approximate is fine; being
    absent is not, because the alternative is discovering it as an OOM after a
    145 GB download.
    """
    import re

    m = re.search(r"(\d+(?:\.\d+)?)\s*[Bb](?![a-zA-Z])", model_id)
    if not m:
        return None
    params = float(m.group(1)) * 1e9
    per = BYTES_PER_PARAM.get((quantization or "").lower(), DEFAULT_BYTES_PER_PARAM)
    return params * per / 1e9


def validate(cfg: dict[str, Any]) -> None:
    """Refuse what would otherwise fail ten minutes into a deploy."""
    topo = cfg["topology"]
    mode = str(topo.get("mode", ""))
    if mode not in POOLS_FOR_MODE:
        raise ConfigError(
            f"topology.mode must be one of {sorted(POOLS_FOR_MODE)}, got {mode!r}"
        )

    expected = POOLS_FOR_MODE[mode]
    declared = set((topo.get("pools") or {}).keys())
    if declared != set(expected):
        raise ConfigError(
            f"topology.mode {mode!r} needs pools {sorted(expected)}, "
            f"but {sorted(declared) or 'none'} are declared. These names are the "
            "gateway's env contract (PREFILL_URLS / DECODE_URLS), not a label."
        )

    split = str(topo.get("split", "phase"))
    if split not in ("phase", "capability"):
        raise ConfigError(f"topology.split must be phase or capability, got {split!r}")

    # A transport that is read nowhere looks configured. One that is missing
    # where it is needed means decode engines with no KV to consume.
    transport = topo.get("kv_transport")
    if mode == "disaggregated" and split == "phase" and not transport:
        raise ConfigError(
            "disaggregated + split=phase needs topology.kv_transport. Decode "
            "engines have no KV to consume without it."
        )
    if mode == "aggregated" and transport:
        raise ConfigError(
            f"topology.kv_transport is set to {transport!r} but mode is aggregated, "
            "where there is no hop. It would be ignored, which is worse than absent."
        )
    if transport and str(transport) != "mooncake":
        print(
            f"note: kv_transport={transport} is a no-op stub that returns immediately. "
            "Hops will be absent from Mooncake's /hops while requests still succeed, "
            "so zero hops will not mean the hop did not happen.",
            file=sys.stderr,
        )

    if cfg.get("model", {}).get("slicing"):
        raise ConfigError(
            "model.slicing is true. A HAMi slice cannot hold these weights, and a "
            "sliced request satisfied by two slices of ONE card would read as TP=2 "
            "across two cards — wrong KV capacity, no NVLink, every number invalid."
        )

    util = float(cfg["engine"]["gpu_memory_utilization"])
    if not 0.0 < util <= 1.0:
        raise ConfigError(f"gpu_memory_utilization must be in (0, 1], got {util}")

    workers = workers_from(cfg)
    if not workers:
        raise ConfigError("the topology expands to zero workers")

    # GPUs are accounted PER HOST. A global count would happily approve two
    # workers needing 4 GPUs spread over two 2-GPU boxes, which cannot run.
    for host_name, host in hosts_from(cfg).items():
        mine = [w for w in workers if w.host.name == host_name]
        if not mine:
            continue
        needed = sum(w.gpus for w in mine)
        if needed > host.gpu_count:
            detail = ", ".join(f"{w.name} ({w.tp}x{w.pp})" for w in mine)
            raise ConfigError(
                f"host {host_name!r} has {host.gpu_count} GPUs but its pools need "
                f"{needed}: {detail}. vLLM would fail after the weights download."
            )
        for w in mine:
            if w.gpus > host.gpu_count:
                raise ConfigError(
                    f"{w.name} wants TP x PP = {w.gpus} GPUs, more than host "
                    f"{host_name!r} has ({host.gpu_count}). A tensor-parallel group "
                    "cannot span hosts — it all-reduces every layer and expects NVLink."
                )

        # The check that catches the expensive mistake: weights that do not fit
        # the card they are sharded onto.
        for w in mine:
            gb = weights_gb(w.model_id, w.quantization)
            if gb is None or not host.hbm_gb:
                continue
            per_card = gb / w.gpus
            usable = host.hbm_gb * util
            headroom = usable - per_card

            # Weights fitting is not the bar. Weights fitting with room left for
            # KV is. Refusing only when per_card > usable passes a config that
            # loads the model and then cannot hold a single sequence — and the
            # parameter count in a model's name is approximate, so a decision
            # taken at the exact boundary is a coin flip. 72B bf16 at TP=2 lands
            # there: ~72.0 GB a card against ~72.0 GB usable.
            if headroom < MIN_KV_HEADROOM_FRACTION * usable:
                raise ConfigError(
                    f"{w.name}: {w.model_id} at {w.quantization or 'bf16'} is ~{gb:.0f} GB, "
                    f"so ~{per_card:.1f} GB per card at TP x PP = {w.gpus}. Host "
                    f"{host_name!r} offers ~{usable:.1f} GB usable "
                    f"({host.hbm_gb:.0f} GB x {util}), leaving ~{headroom:.1f} GB for KV. "
                    "It does not fit with room to serve. The failure would otherwise "
                    "arrive as an OOM after the weights download, or as a cluster that "
                    "starts and then rejects every request."
                )

            # Enough to load and serve, not enough to be worth measuring: one
            # sequence at max_model_len needs several GB of KV per card, so a
            # thin margin means admission bites on capacity we chose, not on
            # pressure the workload created.
            if headroom < 0.25 * usable:
                print(
                    f"note: {w.name} leaves only ~{headroom:.1f} GB per card for KV "
                    f"({headroom / usable:.0%} of usable). Concurrency will be low and "
                    "admission may bite for the wrong reason.",
                    file=sys.stderr,
                )

    # The failure that is quiet rather than loud: a quota refusal must never be
    # pushed onto capacity we pay for.
    on = cfg.get("overflow", {}).get("on") or []
    if 429 in on:
        raise ConfigError(
            "overflow.on includes 429. A tenant over its own quota must not be "
            "sent to a paid provider — that is overspend, not capacity."
        )

    if len({w.model_id for w in workers}) > 1:
        print(
            "note: pools run different models, so they are not interchangeable. "
            "Placement becomes capability routing and the admission comparison "
            "weakens. Fine for a demo; keep them uniform while measuring.",
            file=sys.stderr,
        )

    if not cfg["engine"].get("enable_prefix_caching"):
        print(
            "note: prefix caching is off. An agent resends its system prompt and tool "
            "schemas every turn, so prefill cost is real and the KV wall arrives "
            "sooner. A legitimate axis — but do not compare a policy across this "
            "setting, because the cache state is what changed, not the policy.",
            file=sys.stderr,
        )


def engine_args(cfg: dict[str, Any], w: Worker) -> list[str]:
    e = cfg["engine"]
    args = [
        "--model", w.model_id,
        "--served-model-name", w.model_id, w.served_name,
        "--host", "0.0.0.0",
        "--port", "8000",
        "--max-model-len", str(e["max_model_len"]),
        "--gpu-memory-utilization", str(e["gpu_memory_utilization"]),
        "--max-num-seqs", str(e["max_num_seqs"]),
        "--tensor-parallel-size", str(w.tp),
        "--pipeline-parallel-size", str(w.pp),
        "--distributed-executor-backend", str(e.get("distributed_executor_backend", "mp")),
        "--scheduling-policy", str(e.get("scheduling_policy", "priority")),
    ]
    if e.get("enable_prefix_caching"):
        args.append("--enable-prefix-caching")
    if w.quantization:
        args += ["--quantization", str(w.quantization)]
    if e.get("enable_auto_tool_choice"):
        args.append("--enable-auto-tool-choice")
        if e.get("tool_call_parser"):
            args += ["--tool-call-parser", str(e["tool_call_parser"])]
    return args


def deployment(cfg: dict[str, Any], w: Worker) -> dict[str, Any]:
    labels = {"app": w.name, "pool": w.pool, "capability": w.capability}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": w.name, "labels": labels},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": w.name}},
            "template": {
                "metadata": {
                    "labels": labels,
                    "annotations": {
                        "prometheus.io/scrape": "true",
                        "prometheus.io/port": "8000",
                        "prometheus.io/path": "/metrics",
                    },
                },
                "spec": {
                    "containers": [
                        {
                            "name": "vllm",
                            "image": VLLM_IMAGE,
                            "args": engine_args(cfg, w),
                            "env": [
                                {
                                    "name": "HUGGING_FACE_HUB_TOKEN",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "hf-token",
                                            "key": "token",
                                            "optional": True,
                                        }
                                    },
                                }
                            ],
                            "ports": [
                                {"containerPort": 8000, "hostPort": w.host_port}
                            ],
                            # Whole GPUs. No gpumem/gpucores: a slice cannot hold
                            # these weights, and two slices of one card would
                            # masquerade as TP=2 across two.
                            "resources": {"limits": {"nvidia.com/gpu": str(w.gpus)}},
                            # Omitting this would be a bug: the engine reports
                            # ready minutes before it serves, while torch.compile
                            # captures CUDA-graph buckets.
                            "readinessProbe": {
                                "httpGet": {"path": "/v1/models", "port": 8000},
                                "initialDelaySeconds": 60,
                                "periodSeconds": 15,
                                "failureThreshold": 60,
                            },
                        }
                    ]
                },
            },
        },
    }


def service(w: Worker) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": w.name, "labels": {"pool": w.pool}},
        "spec": {
            "type": "NodePort",
            "selector": {"app": w.name},
            "ports": [{"port": 8000, "targetPort": 8000, "nodePort": w.node_port}],
        },
    }


class _NoAliases(yaml.SafeDumper):
    """Anchors are valid YAML and unreadable in a manifest."""

    def ignore_aliases(self, data: object) -> bool:
        return True


def render(cfg: dict[str, Any], host: str | None = None) -> str:
    """Manifests for one host, or for all of them when host is None."""
    validate(cfg)
    workers = [w for w in workers_from(cfg) if host is None or w.host.name == host]
    if host is not None and not workers:
        raise ConfigError(f"no pools are placed on host {host!r}")
    docs: list[dict[str, Any]] = []
    for w in workers:
        docs.append(deployment(cfg, w))
        docs.append(service(w))
    header = (
        "# GENERATED by infra/render.py — do not edit.\n"
        "# Change infra/config/cluster.yaml and re-render.\n"
        f"# host: {host or 'all'}\n"
    )
    return header + yaml.dump_all(docs, Dumper=_NoAliases, sort_keys=False)


def gateway_env(cfg: dict[str, Any]) -> dict[str, str]:
    """The environment the gateway reads, derived rather than hand-maintained.

    This is the bridge between the config and router/pools.py. Note the overload
    it has to honour: under split=capability the gateway treats PREFILL_URLS as
    the text pool and DECODE_URLS as the vision pool, with no hop involved.
    """
    validate(cfg)
    topo = cfg["topology"]
    mode, split = str(topo["mode"]), str(topo.get("split", "phase"))
    workers = workers_from(cfg)

    def urls(pool: str) -> str:
        return ",".join(
            f"http://{w.host.ssh.split('@')[-1] or '127.0.0.1'}:{w.host_port}"
            for w in workers
            if w.pool == pool
        )

    env = {
        "LAB_TOPOLOGY": mode,
        "LAB_SPLIT": split,
        "LOCAL_MODEL": str(cfg["model"]["id"]),
    }
    if mode == "aggregated":
        # One pool. pools.py treats equal lists as a single set of engines.
        env["PREFILL_URLS"] = urls("engine")
        env["DECODE_URLS"] = env["PREFILL_URLS"]
    else:
        env["PREFILL_URLS"] = urls("prefill")
        env["DECODE_URLS"] = urls("decode")
        if topo.get("kv_transport"):
            env["KV_BACKEND"] = str(topo["kv_transport"])
    return env


def plan(cfg: dict[str, Any]) -> str:
    """What lands where, in one screen, before anything is rented."""
    validate(cfg)
    hosts, workers = hosts_from(cfg), workers_from(cfg)
    topo, util = cfg["topology"], float(cfg["engine"]["gpu_memory_utilization"])
    lines = [
        f"mode={topo['mode']} split={topo.get('split', 'phase')} "
        f"transport={topo.get('kv_transport') or '-'} "
        f"prefix_cache={'on' if cfg['engine'].get('enable_prefix_caching') else 'OFF'}",
        "",
    ]
    for name, host in hosts.items():
        mine = [w for w in workers if w.host.name == name]
        used = sum(w.gpus for w in mine)
        lines.append(f"{name}  {host.gpu_kind} x{host.gpu_count}  using {used}/{host.gpu_count}")
        for w in mine:
            gb = weights_gb(w.model_id, w.quantization)
            fit = ""
            if gb and host.hbm_gb:
                per = gb / w.gpus
                fit = f"  ~{per:.0f}GB/card of {host.hbm_gb * util:.0f}GB usable"
            lines.append(
                f"    {w.name:<20} TP={w.tp} PP={w.pp}  {w.gpus} GPU  "
                f":{w.host_port}  {w.model_id}{fit}"
            )
        if not mine:
            lines.append("    (no pools placed here)")
    lines += ["", "gateway environment:"]
    lines += [f"    {k}={v}" for k, v in gateway_env(cfg).items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a cluster from one config")
    p.add_argument("config", type=Path)
    p.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    p.add_argument("--host", help="only this host's manifests")
    p.add_argument("--env", action="store_true", help="print the gateway environment")
    p.add_argument("--plan", action="store_true", help="print what lands where")
    a = p.parse_args(argv)

    try:
        cfg = load(a.config)
        if a.plan:
            print(plan(cfg))
            return 0
        if a.env:
            for k, v in gateway_env(cfg).items():
                print(f"export {k}={v}")
            return 0
        text = render(cfg, host=a.host)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
