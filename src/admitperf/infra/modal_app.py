"""A Modal app that serves vLLM behind an HTTPS URL.

Deployed by `admitperf infra up`. The whole resolved configuration arrives as
one JSON blob in `ADMITPERF_CONFIG`, rather than as a scatter of individual
environment variables with their own fallbacks. That is deliberate: defaults
belong in `core/config.py` and nowhere else, so a value printed by the CLI is
necessarily the value the deploy used.

Modal evaluates this module locally at deploy time to build the app graph,
which is why configuration has to be readable at import.
"""

from __future__ import annotations

import json
import os
import subprocess

VLLM_PORT = 8000

# Defaults here would be a second source of truth, so there are none: an empty
# object falls through to the dataclass defaults on the caller's side.
_RAW = json.loads(os.environ.get("ADMITPERF_CONFIG", "{}"))

MODEL: str = _RAW.get("model", "Qwen/Qwen3-0.6B")
SERVED_NAME: str = _RAW.get("served_model_name", "lab")
GPU: str = _RAW.get("gpu", "A10G")
SCALEDOWN_S: int = int(_RAW.get("scaledown_window_s", 300))
STARTUP_TIMEOUT_S: int = int(_RAW.get("startup_timeout_s", 900))
ENGINE: dict = _RAW.get("engine", {})

# Gated weights (Llama, Gemma) need a token in the container. Neither is
# required for ungated models, and attaching a secret that does not exist would
# fail the deploy for everyone who never needed one.
#   HF_TOKEN=hf_...           forwarded at deploy time
#   ADMITPERF_HF_SECRET=name  an existing Modal secret, by name
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_SECRET = os.environ.get("ADMITPERF_HF_SECRET", "").strip()

try:
    import modal
except ImportError as exc:  # pragma: no cover - only meaningful with modal installed
    raise SystemExit(
        "modal is not installed. Install the provisioning extra:\n"
        "    pip install 'admitperf[modal]'"
    ) from exc


def _vllm_args() -> list[str]:
    """Rebuild the vLLM argv from the resolved engine config.

    Mirrors EngineConfig.to_vllm_args. It is duplicated rather than imported
    because this module is executed inside Modal's build environment, where
    admitperf itself is not installed.
    """
    e = ENGINE
    args = [
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        "--served-model-name",
        SERVED_NAME,
        "--max-num-seqs",
        str(e.get("max_num_seqs", 8)),
        "--max-model-len",
        str(e.get("max_model_len", 16384)),
        "--gpu-memory-utilization",
        str(e.get("gpu_memory_utilization", 0.90)),
        "--tensor-parallel-size",
        str(e.get("tensor_parallel_size", 1)),
        "--pipeline-parallel-size",
        str(e.get("pipeline_parallel_size", 1)),
        "--scheduling-policy",
        str(e.get("scheduling_policy", "priority")),
        "--dtype",
        str(e.get("dtype", "auto")),
    ]
    args.append(
        "--enable-prefix-caching"
        if e.get("enable_prefix_caching")
        else "--no-enable-prefix-caching"
    )
    if e.get("quantization"):
        args += ["--quantization", str(e["quantization"])]
    if e.get("max_num_batched_tokens"):
        args += ["--max-num-batched-tokens", str(e["max_num_batched_tokens"])]
    if e.get("swap_space_gb"):
        args += ["--swap-space", str(e["swap_space_gb"])]
    if e.get("block_size"):
        args += ["--block-size", str(e["block_size"])]
    return args + [str(a) for a in e.get("extra_args", [])]


image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm>=0.8", "huggingface_hub[hf_transfer]")
    # Weights are the slow part of a cold start; the fast downloader pays for
    # itself on every deploy.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_V1": "1"})
)

app = modal.App("admitperf-vllm")

# Cache weights across deploys, so bringing the same model back is quick.
hf_cache = modal.Volume.from_name("admitperf-hf-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/root/.cache/huggingface": hf_cache},
    # from_dict registers the secret at deploy time. The token is never written
    # into an image layer, so it stays out of the cached build.
    secrets=(
        [modal.Secret.from_dict({"HF_TOKEN": HF_TOKEN})]
        if HF_TOKEN
        else [modal.Secret.from_name(HF_SECRET)]
        if HF_SECRET
        else []
    ),
    timeout=60 * 60,
    # One container. Modal would otherwise autoscale, and a fleet that grows
    # under load measures elasticity rather than admission control.
    max_containers=1,
    scaledown_window=SCALEDOWN_S,
)
@modal.web_server(port=VLLM_PORT, startup_timeout=STARTUP_TIMEOUT_S)
def serve() -> None:
    cmd = ["vllm", "serve", MODEL, *_vllm_args()]
    print("launching:", " ".join(cmd), flush=True)
    subprocess.Popen(" ".join(cmd), shell=True)
