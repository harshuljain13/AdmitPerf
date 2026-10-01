"""Render worker manifests from inference_cluster/config/cluster.yaml.

One config in, N vLLM Deployments and Services out. The point is that model,
topology and engine flags appear exactly once. Hand-edited manifests make
"same cluster, only the policy changed" a claim nobody can check.

    python -m inference_cluster.render inference_cluster/config/cluster.yaml            # print
    python -m inference_cluster.render inference_cluster/config/cluster.yaml -o out.yaml

Static manifests (gateway, mooncake, open-webui, observability) are not
generated — they do not vary with the model.
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


class ConfigError(ValueError):
    """The config cannot produce a cluster. Raised before anything is rented."""


@dataclass(frozen=True)
class Worker:
    index: int
    model_id: str
    served_name: str
    capability: str
    quantization: str | None

    @property
    def name(self) -> str:
        return f"vllm-worker-{self.index}"

    @property
    def host_port(self) -> int:
        return BASE_HOST_PORT + self.index

    @property
    def node_port(self) -> int:
        return BASE_NODE_PORT + self.index


def load(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a mapping")
    return raw


def validate(cfg: dict[str, Any]) -> None:
    """Refuse what would otherwise fail ten minutes into a deploy."""
    topo, gpu = cfg["topology"], cfg["gpu"]
    workers = int(topo["workers"])
    tp = int(topo["tensor_parallel_size"])
    pp = int(topo.get("pipeline_parallel_size", 1))

    if workers < 1:
        raise ConfigError(f"topology.workers must be >= 1, got {workers}")

    needed = workers * tp * pp
    have = int(gpu["count"])
    if needed > have:
        raise ConfigError(
            f"workers × TP × PP = {workers} × {tp} × {pp} = {needed} GPUs, "
            f"but gpu.count is {have}. vLLM would fail after the weights "
            "download — cheaper to refuse here."
        )

    util = float(cfg["engine"]["gpu_memory_utilization"])
    if not 0.0 < util <= 1.0:
        raise ConfigError(f"gpu_memory_utilization must be in (0, 1], got {util}")

    # The failure that is quiet rather than loud: a quota refusal must never be
    # pushed onto capacity we pay for.
    on = cfg.get("overflow", {}).get("on") or []
    if 429 in on:
        raise ConfigError(
            "overflow.on includes 429. A tenant over its own quota must not be "
            "sent to a paid provider — that is overspend, not capacity."
        )

    if workers > 1 and _worker_models(cfg) and len(set(_worker_models(cfg))) > 1:
        print(
            "note: workers run different models, so they are not interchangeable. "
            "Placement becomes capability routing and policy comparison weakens. "
            "Fine for a demo; keep them uniform while measuring.",
            file=sys.stderr,
        )


def _worker_models(cfg: dict[str, Any]) -> list[str]:
    return [w.model_id for w in workers_from(cfg)]


def workers_from(cfg: dict[str, Any]) -> list[Worker]:
    base = cfg["model"]
    overrides = {int(o["index"]): o for o in cfg.get("workers_override") or []}
    out: list[Worker] = []
    for i in range(int(cfg["topology"]["workers"])):
        o = overrides.get(i, {})
        m = o.get("model", {})
        out.append(
            Worker(
                index=i,
                model_id=m.get("id", base["id"]),
                served_name=m.get("served_name", base.get("served_name", "lab")),
                capability=m.get("capability", base.get("capability", "text")),
                quantization=m.get("quantization", base.get("quantization")),
            )
        )
    return out


def engine_args(cfg: dict[str, Any], w: Worker) -> list[str]:
    e, topo = cfg["engine"], cfg["topology"]
    args = [
        "--model", w.model_id,
        "--served-model-name", w.model_id, w.served_name,
        "--host", "0.0.0.0",
        "--port", "8000",
        "--max-model-len", str(e["max_model_len"]),
        "--gpu-memory-utilization", str(e["gpu_memory_utilization"]),
        "--max-num-seqs", str(e["max_num_seqs"]),
        "--tensor-parallel-size", str(topo["tensor_parallel_size"]),
        "--pipeline-parallel-size", str(topo.get("pipeline_parallel_size", 1)),
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
    tp = int(cfg["topology"]["tensor_parallel_size"])
    pp = int(cfg["topology"].get("pipeline_parallel_size", 1))
    labels = {"app": w.name, "capability": w.capability}
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
                            "resources": {"limits": {"nvidia.com/gpu": str(tp * pp)}},
                            # No readiness probe on purpose would be a bug: the
                            # engine reports ready minutes before it serves.
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
        "metadata": {"name": w.name},
        "spec": {
            "type": "NodePort",
            "selector": {"app": w.name},
            "ports": [{"port": 8000, "targetPort": 8000, "nodePort": w.node_port}],
        },
    }


def render(cfg: dict[str, Any]) -> str:
    validate(cfg)
    docs: list[dict[str, Any]] = []
    for w in workers_from(cfg):
        docs.append(deployment(cfg, w))
        docs.append(service(w))
    header = (
        "# GENERATED by inference_cluster/render.py — do not edit.\n"
        "# Change the config and re-render.\n"
    )
    class _NoAliases(yaml.SafeDumper):
        """Anchors are valid YAML and unreadable in a manifest."""

        def ignore_aliases(self, data: object) -> bool:
            return True

    return header + yaml.dump_all(docs, Dumper=_NoAliases, sort_keys=False)


def worker_urls(cfg: dict[str, Any], host: str = "127.0.0.1") -> list[str]:
    """Where the gateway should look for each worker."""
    return [f"http://{host}:{w.host_port}" for w in workers_from(cfg)]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render worker manifests from one config")
    p.add_argument("config", type=Path)
    p.add_argument("-o", "--out", type=Path, help="write here instead of stdout")
    p.add_argument("--urls", action="store_true", help="print worker URLs and exit")
    a = p.parse_args(argv)

    cfg = load(a.config)
    if a.urls:
        print("\n".join(worker_urls(cfg)))
        return 0
    text = render(cfg)
    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
