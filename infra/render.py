"""Turn a cluster config into manifests and the gateway's environment.

One config in; per-host Kubernetes manifests out, plus the URL lists the gateway
reads. Model, topology and engine flags appear exactly once. Hand-edited manifests
make "same cluster, only the policy changed" a claim nobody can check.

Three runnable configs, each extending base.yaml, so the shared settings are
declared once and only the topology differs:

    python -m infra.render infra/config/single.yaml --plan   # 1 GPU, aggregated
    python -m infra.render infra/config/pair.yaml --plan     # 2 GPUs, aggregated
    python -m infra.render infra/config/disagg.yaml --plan   # 4 GPUs, disagg 2+2

    python -m infra.render infra/config/pair.yaml --env      # gateway env
    python -m infra.render infra/config/pair.yaml --host gpu-1

Static manifests (gateway, mooncake, open-webui, observability) are not
generated — they do not vary with the model.

Hosts are INDEPENDENT single-node clusters. The gateway federates by URL list,
so a second box is a second set of URLs rather than a second control plane. A
tensor-parallel group therefore cannot span hosts, which is the correct
restriction anyway: TP all-reduces every layer and expects NVLink.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
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

# Compute capability by card, and what each generation can actually execute.
# fp8 tensor cores arrived with Hopper (sm90) and Ada (sm89). On an A100 (sm80)
# an fp8 checkpoint fails AFTER the weights download, which is the most expensive
# way to learn this.
GPU_COMPUTE_CAPABILITY = {
    "H100": 9.0,
    "H200": 9.0,
    "GH200": 9.0,
    "L40S": 8.9,
    "L4": 8.9,
    "RTX4090": 8.9,
    "A100": 8.0,
    "A10": 8.6,
    "A6000": 8.6,
    "A40": 8.6,
    "V100": 7.0,
    "T4": 7.5,
}
MIN_CAPABILITY_FOR = {"fp8": 8.9}

# Bytes of KV per token, for the capacity estimate:
#   2 (K and V) x layers x kv_heads x head_dim x dtype_bytes
# Grouped-query attention means kv_heads is usually far below attention heads,
# which is why these numbers are smaller than people expect.
KV_DTYPE_BYTES = 2


class ConfigError(ValueError):
    """The config cannot produce a cluster. Raised before anything is rented."""


@dataclass(frozen=True)
class Host:
    """A machine's SHAPE. Its address and key are not here on purpose.

    An IP and a private key are facts about today, not about the topology. They
    live in .env so that re-renting a box is an edit, not a commit — and so that
    a key path is never committed at all.
    """

    name: str
    gpu_kind: str
    gpu_count: int
    hbm_gb: float
    #: Name of the .env variable holding this host's PUBLIC address, used for SSH
    #: and rsync. The NAME is not a secret so it belongs in the config; the
    #: address is, so it does not.
    address_env: str | None = None

    #: Name of the variable holding its PRIVATE address. Lambda firewalls the
    #: public interface down to port 22, so worker URLs and the KV store must use
    #: the private network — 10.19.x.x here. Falls back to the public address when
    #: unset, which is correct for a single host reached through a tunnel.
    private_env: str | None = None

    @property
    def env_suffix(self) -> str:
        return self.name.upper().replace("-", "_").replace(".", "_")

    def address(self, env: Mapping[str, str], *, sole: bool = False) -> str:
        """ubuntu@1.2.3.4 from the environment, or "" when unset.

        LAMBDA is accepted only when there is exactly one host. With two boxes it
        is ambiguous, and silently applying it to both would point every URL at
        one machine while the plan claimed two.
        """
        if self.address_env:
            return env.get(self.address_env, "").strip()
        # Falls back to a name-derived variable when the config does not name one,
        # then to LAMBDA for a single host so the lab's scripts keep working.
        specific = env.get(f"LAMBDA_HOST_{self.env_suffix}", "").strip()
        if specific:
            return specific
        return env.get("LAMBDA", "").strip() if sole else ""

    def data_address(self, env: Mapping[str, str], *, sole: bool = False) -> str:
        """The address OTHER MACHINES use to reach this one.

        Separate from `address` because they are genuinely different networks. The
        public interface accepts only SSH, so a worker URL built from it connects
        to nothing — and the failure is a timeout during a run rather than an error
        at configuration time.
        """
        if self.private_env:
            got = env.get(self.private_env, "").strip()
            if got:
                return got
            if not sole:
                # Falling back to the public address here is how a run dies in the
                # middle rather than at render time: the public interface accepts
                # only SSH, so the URL resolves, connects to nothing, and times out.
                # Only a single host behind a tunnel can legitimately use its public
                # address as a data address.
                return ""
        return self.address(env, sole=sole)

    def key(self, env: Mapping[str, str]) -> str:
        return (
            env.get(f"LAMBDA_SSH_KEY_{self.env_suffix}", "").strip()
            or env.get("LAMBDA_SSH_KEY", "").strip()
        )

    def env_var(self) -> str:
        return self.address_env or f"LAMBDA_HOST_{self.env_suffix}"


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


def load_dotenv(root: Path | None = None) -> dict[str, str]:
    """Read .env the way the bring-up scripts do, without adding a dependency.

    Already-exported variables win, matching `set -a; source .env` — so a one-off
    `LAMBDA_HOST_GPU_A=... python -m infra.render --env` still overrides the file.

    Without this, --env silently reports every address as unset while .env sits
    right there, which reads as "the config is broken" rather than "nothing read
    the file".
    """
    root = root or Path(__file__).resolve().parent.parent
    out: dict[str, str] = {}
    path = root / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out[k.strip()] = v
    out.update({k: v for k, v in os.environ.items() if v})
    return out


#: Keys whose child value REPLACES the parent's rather than merging into it. A
#: variant declaring `engine.max_model_len` should inherit the other engine flags,
#: but one declaring pools must not inherit the parent's — half a pool set is not a
#: topology.
REPLACE_WHOLE = frozenset({"pools", "on"})


def _merge(base: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge child over base. Lists replace; REPLACE_WHOLE keys replace."""
    out = dict(base)
    for key, value in child.items():
        if (
            key not in REPLACE_WHOLE
            and isinstance(value, dict)
            and isinstance(out.get(key), dict)
        ):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path, _seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Read a config, following `extends` so shared values are declared once.

        # pair.yaml
        extends: base.yaml
        topology: {...}

    base.yaml holds hosts, model, engine, placement, overflow and admission, and
    deliberately declares NO topology — that is the thing each variant changes. So
    when two runs differ only by which file was rendered, "same cluster, only the
    topology changed" is true by construction rather than by discipline.

    Paths are relative to the file doing the extending, so a config directory can
    be copied or moved whole.
    """
    path = path.resolve()
    if path in _seen:
        chain = " -> ".join(q.name for q in (*_seen, path))
        raise ConfigError(f"extends forms a cycle: {chain}")

    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a mapping")

    parent_ref = raw.pop("extends", None)
    if not parent_ref:
        raw.setdefault("config_name", path.stem)
        return raw

    parent_path = (path.parent / str(parent_ref)).resolve()
    if not parent_path.exists():
        raise ConfigError(
            f"{path.name} extends {parent_ref!r}, which does not exist "
            f"(looked in {path.parent})"
        )
    merged = _merge(load(parent_path, (*_seen, path)), raw)
    # Recorded so a plan, and later a run bundle, says which variant produced it.
    merged["config_name"] = path.stem
    return merged


def hosts_from(
    cfg: dict[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Host]:
    """The machines, either listed explicitly or derived from an inventory.

    Every host is declared, with its own GPU kind, count and HBM, because a fleet
    is not necessarily uniform. Each names the .env variable holding its address:

        hosts:
          - name: gpu-1
            address_env: LAMBDA_HOST_1
            gpu: {kind: A100, count: 1, hbm_gb: 40}

    The variable NAME is not a secret, so it lives in the committed config. The
    address is, so it does not.
    """
    raw = cfg.get("hosts") or []

    if not raw:
        raise ConfigError("no hosts declared. At least one is required.")
    out: dict[str, Host] = {}
    for h in raw:
        name = str(h["name"])
        if name in out:
            raise ConfigError(f"duplicate host name {name!r}")
        gpu = h.get("gpu") or {}
        for banned in ("ssh", "ssh_key", "address", "ip"):
            if banned in h:
                raise ConfigError(
                    f"host {name!r} declares {banned!r}. Addresses and keys do not belong "
                    f"in a committed config — set {Host(name, '', 0, 0).env_var()} in .env "
                    "instead."
                )
        out[name] = Host(
            name=name,
            address_env=(str(h["address_env"]) if h.get("address_env") else None),
            private_env=(str(h["private_env"]) if h.get("private_env") else None),
            gpu_kind=str(gpu.get("kind", "unknown")),
            gpu_count=int(gpu.get("count", 0)),
            hbm_gb=float(gpu.get("hbm_gb", 0)),
        )
    return out


def workers_from(
    cfg: dict[str, Any], env: Mapping[str, str] | None = None
) -> list[Worker]:
    """Expand pools into one Worker per replica, numbered per host.

    A pool naming a `host` is pinned there. A pool without one is PLACED, by
    filling hosts in declaration order until each is full. The assignment is
    deterministic given the same config and the same inventory order, and `--plan`
    prints it — an assignment that moved silently when .env changed would mean a
    rerun measured a different deployment while claiming to be a repeat.
    """
    hosts = hosts_from(cfg, env)
    topo = cfg["topology"]
    model = cfg["model"]
    pools = topo.get("pools") or {}
    per_host_index: dict[str, int] = dict.fromkeys(hosts, 0)
    free: dict[str, int] = {n: h.gpu_count for n, h in hosts.items()}
    out: list[Worker] = []

    # Pinned pools claim their GPUs first, so placement works around them rather
    # than handing out cards a pinned pool is going to need.
    for spec in (topo.get("pools") or {}).values():
        pinned_at = spec.get("host")
        if pinned_at in free:
            need = int(spec.get("replicas", 1)) * int(
                spec.get("tensor_parallel_size", 1)
            ) * int(spec.get("pipeline_parallel_size", 1))
            free[pinned_at] -= need

    def place(need: int) -> str:
        """First host in declaration order with room. Not least-loaded: stable
        beats balanced here, because a stable mapping is what makes two runs
        comparable."""
        for name, left in free.items():
            if left >= need:
                return name
        raise ConfigError(
            f"no host has {need} free GPU(s) for an unpinned pool. "
            f"Remaining: {', '.join(f'{n}:{v}' for n, v in free.items())}"
        )

    # Iterate in the mode's declared pool order so ports and URL lists are
    # stable across renders. Dict order would depend on how the YAML was typed.
    for pool_name in POOLS_FOR_MODE.get(str(topo.get("mode", "")), ()):
        spec = pools.get(pool_name)
        if spec is None:
            continue
        declared = str(spec.get("host", "") or "")
        if declared and declared != "auto" and declared not in hosts:
            raise ConfigError(
                f"pool {pool_name!r} names host {declared!r}, which is not "
                f"declared. Known hosts: {', '.join(hosts) or 'none'}"
            )
        pinned = declared if declared and declared != "auto" else None
        tp = int(spec.get("tensor_parallel_size", 1))
        pp = int(spec.get("pipeline_parallel_size", 1))
        per = tp * pp
        m = spec.get("model") or {}

        for replica in range(int(spec.get("replicas", 1))):
            # Placed PER REPLICA, not per pool. A replica is an independent
            # server, so two replicas of one pool may sit on different boxes —
            # only a tensor-parallel group is forbidden from spanning hosts,
            # because it all-reduces every layer. Placing per pool meant six
            # replicas could not use two four-GPU hosts, which is a real
            # topology and was refused for no reason.
            if pinned:
                host_name = pinned
            else:
                host_name = place(per)
                free[host_name] -= per
            host = hosts[host_name]
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
                    tp=tp,
                    pp=pp,
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


def kv_bytes_per_token(cfg: dict[str, Any]) -> int | None:
    """From the config's declared attention shape, or None if it is not given.

    Declared rather than fetched: reading config.json needs the weights, and this
    number decides whether the experiment can work at all, so it has to be
    available before anything is rented. Verify it against the model's config.json
    once the cluster is up — an estimate that is wrong by 2x moves the predicted
    concurrency by 2x.
    """
    a = cfg.get("model", {}).get("attention") or {}
    try:
        return (
            2
            * int(a["layers"])
            * int(a["kv_heads"])
            * int(a["head_dim"])
            * int(a.get("dtype_bytes", KV_DTYPE_BYTES))
        )
    except (KeyError, TypeError, ValueError):
        return None


def concurrency_estimate(cfg: dict[str, Any], kv_gb_total: float) -> float | None:
    """Sequences that fit at max_model_len. The number the experiment lives on.

    If this is comfortably above max_num_seqs, the scheduler caps concurrency
    before KV does and a KV-pressure policy never fires — which is exactly how
    `half-capacity-headroom` produced three runs of flat signals.
    """
    per_token = kv_bytes_per_token(cfg)
    if not per_token:
        return None
    per_seq = per_token * int(cfg["engine"]["max_model_len"])
    return kv_gb_total * 1e9 / per_seq


def validate(cfg: dict[str, Any], env: Mapping[str, str] | None = None) -> None:
    """Refuse what would otherwise fail ten minutes into a deploy.

    Takes `env` because the host set can come from an inventory variable, and a
    check whose result depends on the machine it runs on is not a check.
    """
    topo = cfg["topology"]
    mode = str(topo.get("mode", ""))
    if mode not in POOLS_FOR_MODE:
        raise ConfigError(f"topology.mode must be one of {sorted(POOLS_FOR_MODE)}, got {mode!r}")

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
    if str(transport) == "mooncake" and not topo.get("kv_endpoint"):
        raise ConfigError(
            "kv_transport is mooncake but topology.kv_endpoint is unset. The hop "
            "would then be a silent no-op: _store_put returns without posting and "
            "logs nothing, so zero hops on the dashboard would look like a "
            "measurement rather than a missing setting."
        )
    if transport and str(transport) not in ("mooncake",) and topo.get("kv_endpoint"):
        raise ConfigError(
            f"topology.kv_endpoint is set but kv_transport is {transport!r}, which "
            "is not URL-addressed — nccl is a collective and nixl is an RDMA path. "
            "It would be read by nothing while looking configured."
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

    workers = workers_from(cfg, env)
    if not workers:
        raise ConfigError("the topology expands to zero workers")

    # GPUs are accounted PER HOST. A global count would happily approve two
    # workers needing 4 GPUs spread over two 2-GPU boxes, which cannot run.
    for host_name, host in hosts_from(cfg, env).items():
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

        # A quantization the silicon cannot execute. Checked before the fit,
        # because "it does not fit" is misleading when the real answer is "this
        # card cannot run that format at all".
        cap = GPU_COMPUTE_CAPABILITY.get(host.gpu_kind.upper())
        for w in mine:
            need = MIN_CAPABILITY_FOR.get((w.quantization or "").lower())
            if need is None or cap is None:
                continue
            if cap < need:
                raise ConfigError(
                    f"{w.name}: quantization {w.quantization!r} needs compute capability "
                    f"{need}, but host {host_name!r} is a {host.gpu_kind} (sm{int(cap * 10)}). "
                    f"fp8 tensor cores arrived with Hopper and Ada; on this card the "
                    f"checkpoint fails after the weights download. Use int8, awq or "
                    f"gptq, or bf16 if it fits."
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

            # Headroom as a FRACTION is not the test; headroom measured in
            # sequences is. A config can clear the 5% floor and still be unable
            # to hold one request at max_model_len — 32B int8 on a 40 GB card
            # leaves 4 GB, which is 11% of usable and zero sequences. That loads
            # the model and then rejects everything.
            kv_total = headroom * w.gpus
            seqs = concurrency_estimate(cfg, kv_total)
            if seqs is not None and seqs < 1:
                raise ConfigError(
                    f"{w.name}: {kv_total:.1f} GB of KV across {w.gpus} card(s) holds "
                    f"{seqs:.2f} sequences at max_model_len={cfg['engine']['max_model_len']}. "
                    "Fewer than one. The engine would start and then refuse every "
                    "request. Raise TP, quantize further, or lower max_model_len."
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
        "--model",
        w.model_id,
        "--served-model-name",
        w.model_id,
        w.served_name,
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--max-model-len",
        str(e["max_model_len"]),
        "--gpu-memory-utilization",
        str(e["gpu_memory_utilization"]),
        "--max-num-seqs",
        str(e["max_num_seqs"]),
        "--tensor-parallel-size",
        str(w.tp),
        "--pipeline-parallel-size",
        str(w.pp),
        "--distributed-executor-backend",
        str(e.get("distributed_executor_backend", "mp")),
        "--scheduling-policy",
        str(e.get("scheduling_policy", "priority")),
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
                            "ports": [{"containerPort": 8000, "hostPort": w.host_port}],
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


def serve_commands(
    cfg: dict[str, Any], host: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    """`vllm serve` command lines, for a bare VM with no Kubernetes.

    A rented GPU box is an Ubuntu machine with SSH and a driver. The manifests
    assume a cluster it does not have, so without this the bring-up step is a
    hand-written command line — and a hand-written one drifts from the config in
    exactly the settings that decide the experiment. `infra/setup/lambda_vllm.sh`
    is the proof: it hardcodes gpu-memory-utilization 0.85 against the config's
    0.90 and omits --max-num-seqs altogether, which is the single flag that
    decides whether a KV-pressure signal can reach its threshold.

    Built from `engine_args`, the same function the manifests use, so the two
    cannot disagree about what is being served.
    """
    validate(cfg, env)
    workers = [w for w in workers_from(cfg, env) if host is None or w.host.name == host]
    if host is not None and not workers:
        raise ConfigError(f"no pools are placed on host {host!r}")

    out = [
        "# GENERATED by infra/render.py — do not edit.",
        f"# Change infra/config/{cfg.get('config_name', '')}.yaml and re-render.",
        "# Run on the GPU box. One engine per line; each needs its own shell.",
        "",
    ]
    # Each engine on a host gets its own contiguous slice of that host's cards.
    # Without this two engines on one box both grab GPU 0 and the second dies
    # out of memory, which reads as a weights-too-big problem and is not one.
    cursor: dict[str, int] = {}
    for w in workers:
        args = engine_args(cfg, w)
        # --model is positional for `vllm serve`, and the port is per engine
        # rather than the container's fixed 8000.
        flags: list[str] = []
        skip = False
        for a in args:
            if skip:
                skip = False
                continue
            if a == "--model":
                skip = True
                continue
            if a == "--port":
                flags += ["--port", str(w.host_port)]
                skip = True
                continue
            flags.append(a)

        start = cursor.get(w.host.name, 0)
        cursor[w.host.name] = start + w.gpus
        same_host = sum(1 for other in workers if other.host.name == w.host.name)

        out.append(f"# {w.name} on {w.host.name} ({w.host.gpu_kind} x{w.gpus}) -> :{w.host_port}")
        if same_host > 1:
            cards = ",".join(str(start + i) for i in range(w.gpus))
            out.append(f"CUDA_VISIBLE_DEVICES={cards} \\")
        out.append(f"vllm serve {w.model_id} \\")
        out.append("  " + " \\\n  ".join(_pairs(flags)))
        out.append("")
    return "\n".join(out)


def _pairs(flags: list[str]) -> list[str]:
    """Group `--flag value` onto one line each, so the command is readable."""
    lines: list[str] = []
    i = 0
    while i < len(flags):
        if i + 1 < len(flags) and not flags[i + 1].startswith("--"):
            run = [flags[i]]
            i += 1
            while i < len(flags) and not flags[i].startswith("--"):
                run.append(flags[i])
                i += 1
            lines.append(" ".join(run))
        else:
            lines.append(flags[i])
            i += 1
    return lines


class _NoAliases(yaml.SafeDumper):
    """Anchors are valid YAML and unreadable in a manifest."""

    def ignore_aliases(self, data: object) -> bool:
        return True


def render(
    cfg: dict[str, Any], host: str | None = None, env: Mapping[str, str] | None = None
) -> str:
    """Manifests for one host, or for all of them when host is None."""
    validate(cfg, env)
    workers = [w for w in workers_from(cfg, env) if host is None or w.host.name == host]
    if host is not None and not workers:
        raise ConfigError(f"no pools are placed on host {host!r}")
    docs: list[dict[str, Any]] = []
    for w in workers:
        docs.append(deployment(cfg, w))
        docs.append(service(w))
    header = (
        "# GENERATED by infra/render.py — do not edit.\n"
        f"# Change infra/config/{cfg.get('config_name', '')}.yaml and re-render.\n"
        f"# host: {host or 'all'}\n"
    )
    return header + yaml.dump_all(docs, Dumper=_NoAliases, sort_keys=False)


def gateway_env(
    cfg: dict[str, Any], env: Mapping[str, str] | None = None, *, strict: bool = True
) -> dict[str, str]:
    """The environment the gateway reads, derived rather than hand-maintained.

    This is the bridge between the config and router/pools.py. Host addresses come
    from `env` (your .env), because they are not topology. With strict=True an
    unset address is an error naming the variable to set; --plan passes
    strict=False so it still works offline.

    Note the overload this has to honour: under split=capability the gateway
    treats PREFILL_URLS as the text pool and DECODE_URLS as the vision pool, with
    no hop involved.

    OVERFLOW_API_KEY is deliberately absent. It is the one secret in the overflow
    block and it stays in .env; everything else is emitted from the config so it
    cannot be typed twice and drift.
    """
    env = load_dotenv() if env is None else dict(env)
    validate(cfg, env)
    topo = cfg["topology"]
    mode, split = str(topo["mode"]), str(topo.get("split", "phase"))
    workers = workers_from(cfg, env)
    hosts = hosts_from(cfg, env)
    # Counted over hosts that actually run something, not over hosts declared.
    # base.yaml declares the whole fleet and single.yaml uses one of them, so a
    # count of declarations would make the single case look multi-host and reject
    # the LAMBDA shorthand it is entitled to.
    in_use = {w.host.name for w in workers}
    sole = len(in_use) == 1

    addr: dict[str, str] = {}
    for name, host in hosts.items():
        if not any(w.host.name == name for w in workers):
            continue
        # The PRIVATE address: these URLs are used by the gateway and by workers
        # reaching each other, and the public interface accepts only SSH.
        a = host.data_address(env, sole=sole)
        if not a:
            if strict:
                if host.private_env and not sole:
                    raise ConfigError(
                        f"host {name!r} has no PRIVATE address. Set "
                        f"{host.private_env} in .env. Its public address will not "
                        "do: that interface accepts only SSH, so a worker URL "
                        "built from it connects to nothing and fails as a timeout "
                        "mid-run rather than an error here."
                    )
                hint = f" (or LAMBDA, since {name!r} is the only host)" if sole else ""
                raise ConfigError(
                    f"host {name!r} has no address. Set {host.env_var()} in .env{hint}. "
                    "It is not in the config because an IP is a fact about today."
                )
            a = f"<{host.env_var()}-unset>"
        addr[name] = a

    def urls(pool: str) -> str:
        return ",".join(
            f"http://{addr[w.host.name].split('@')[-1]}:{w.host_port}"
            for w in workers
            if w.pool == pool
        )

    out = {
        "LAB_TOPOLOGY": mode,
        "LAB_SPLIT": split,
        "LOCAL_MODEL": str(cfg["model"]["id"]),
    }
    if mode == "aggregated":
        # One pool. pools.py treats equal lists as a single set of engines.
        out["PREFILL_URLS"] = urls("engine")
        out["DECODE_URLS"] = out["PREFILL_URLS"]
    else:
        out["PREFILL_URLS"] = urls("prefill")
        out["DECODE_URLS"] = urls("decode")
        if topo.get("kv_transport"):
            out["KV_BACKEND"] = str(topo["kv_transport"])
            # Mooncake is an HTTP service and reads MOONCAKE_URL. Emitting it from
            # the config is what stops it being unset: _store_put returns silently
            # when it is, so every hop becomes a no-op with nothing logged.
            if str(topo["kv_transport"]) == "mooncake" and topo.get("kv_endpoint"):
                out["MOONCAKE_URL"] = str(topo["kv_endpoint"])

    ov = cfg.get("overflow") or {}
    if ov.get("base_url"):
        out["OVERFLOW_BACKEND"] = str(ov.get("provider", ""))
        out["OVERFLOW_BASE_URL"] = str(ov["base_url"])
        out["OVERFLOW_MODEL"] = str(ov.get("model", ""))
        out["OVERFLOW_MAX_REQS"] = str(ov.get("max_requests", ""))
        out["OVERFLOW_MAX_TOKENS"] = str(ov.get("max_tokens", ""))
    return out


def plan(cfg: dict[str, Any], env: Mapping[str, str] | None = None) -> str:
    """What lands where, in one screen, before anything is rented.

    Takes `env` explicitly so a caller — a test especially — is never at the mercy
    of whether a .env happens to exist on the machine running it.
    """
    env = load_dotenv() if env is None else dict(env)
    validate(cfg, env)
    hosts, workers = hosts_from(cfg, env), workers_from(cfg, env)
    topo, util = cfg["topology"], float(cfg["engine"]["gpu_memory_utilization"])
    lines = []
    if cfg.get("config_name"):
        lines.append(f"config={cfg['config_name']}")
    lines += [
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
            if gb and host.hbm_gb:
                kv_total = (host.hbm_gb * util - gb / w.gpus) * w.gpus
                n = concurrency_estimate(cfg, kv_total)
                if n is not None:
                    cap = int(cfg["engine"]["max_num_seqs"])
                    mml = int(cfg["engine"]["max_model_len"])
                    # Which constraint binds is NOT a property of this file. The cache
                    # fills with `max_num_seqs x tokens_per_request` tokens, and the
                    # request length comes from the experiment. Comparing seqs-at-
                    # max_model_len against max_num_seqs assumes every request uses the
                    # full context; at a realistic length it is wrong by the ratio
                    # between them, and it reported "KV binds" for a workload whose
                    # kv_used_fraction could not pass 0.35.
                    pool_tokens = n * mml
                    crossover = pool_tokens / cap
                    lines.append(
                        f"    {'':<20} {kv_total:.0f}GB KV = ~{pool_tokens / 1000:.0f}k tokens"
                        f"  (~{n:.0f} seqs at max_model_len {mml})"
                    )
                    lines.append(
                        f"    {'':<20} max_num_seqs={cap} -> KV binds only above "
                        f"~{crossover / 1000:.1f}k tokens/request; below that the "
                        f"SCHEDULER binds"
                    )
                    lines.append(
                        f"    {'':<20} so a KV policy can fire only if the experiment "
                        f"sends requests longer than ~{crossover / 1000:.1f}k tokens"
                    )
        if not mine:
            lines.append("    (no pools placed here)")
    lines += ["", "gateway environment:"]
    lines += [f"    {k}={v}" for k, v in gateway_env(cfg, env, strict=False).items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a cluster from one config")
    p.add_argument("config", type=Path)
    p.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    p.add_argument("--host", help="only this host's manifests")
    p.add_argument("--env", action="store_true", help="print the gateway environment")
    p.add_argument("--plan", action="store_true", help="print what lands where")
    p.add_argument(
        "--serve",
        action="store_true",
        help="print the `vllm serve` command lines, for a bare GPU box with no cluster",
    )
    a = p.parse_args(argv)

    try:
        cfg = load(a.config)
        if a.plan:
            print(plan(cfg))
            return 0
        if a.serve:
            # A rented GPU box is an Ubuntu machine with a driver and no cluster, so
            # the manifests are useless there. Generated from the same config as the
            # manifests, so the bare-VM path cannot drift from it in the settings that
            # decide the experiment — `infra/setup/lambda_vllm.sh` hardcodes
            # gpu-memory-utilization 0.85 and omits --max-num-seqs, which is exactly
            # the flag that decides whether a KV policy can fire at all.
            print(serve_commands(cfg, host=a.host))
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
