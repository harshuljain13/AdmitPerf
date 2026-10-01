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

# This module is imported TWICE, and the difference matters.
#
#   1. Locally, by `modal deploy`, to build the app graph. ADMITPERF_CONFIG is
#      present here, and this is when decorator arguments like gpu= are read.
#   2. Again inside the container, at startup, to call serve(). The deploy
#      environment is gone by then.
#
# So the config is baked into the image below via .env(), which is what makes
# it visible in case 2. Reading os.environ at module level alone is not enough:
# the first deploy of this app silently served the default model because the
# container had no ADMITPERF_CONFIG to read.
_RAW_JSON = os.environ.get("ADMITPERF_CONFIG", "")
if not _RAW_JSON:
    raise SystemExit(
        "ADMITPERF_CONFIG is not set. This module is deployed by "
        "`admitperf infra up`, which sets it; running `modal deploy` on it "
        "directly would serve an unspecified configuration."
    )
_RAW = json.loads(_RAW_JSON)

MODEL: str = _RAW["model"]
SERVED_NAME: str = _RAW["served_model_name"]
GPU: str = _RAW["gpu"]
SCALEDOWN_S: int = int(_RAW["scaledown_window_s"])
STARTUP_TIMEOUT_S: int = int(_RAW["startup_timeout_s"])
ENGINE: dict = _RAW["engine"]

#: Concurrent HTTP requests one container accepts. Deliberately far above
#: max_num_seqs: the engine's admission limit must be the bottleneck, not
#: Modal's proxy.
MAX_CONCURRENT_INPUTS: int = int(_RAW.get("max_concurrent_inputs", 256))

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

    Mirrors EngineConfig.to_vllm_args. Duplicated rather than imported because
    this runs inside Modal's container, where admitperf is not installed.

    Indexing rather than .get(): a missing key means the config did not arrive,
    and serving a default nobody asked for is worse than refusing to start.
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
        str(e["max_num_seqs"]),
        "--max-model-len",
        str(e["max_model_len"]),
        "--gpu-memory-utilization",
        str(e["gpu_memory_utilization"]),
        "--tensor-parallel-size",
        str(e["tensor_parallel_size"]),
        "--pipeline-parallel-size",
        str(e["pipeline_parallel_size"]),
        "--scheduling-policy",
        str(e["scheduling_policy"]),
        "--dtype",
        str(e["dtype"]),
    ]
    args.append(
        "--enable-prefix-caching" if e["enable_prefix_caching"] else "--no-enable-prefix-caching"
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
    # ADMITPERF_CONFIG must live in the image, not just the deploy shell, or
    # the container falls back to defaults. HF_XET_HIGH_PERFORMANCE replaces
    # HF_HUB_ENABLE_HF_TRANSFER, which current huggingface_hub deprecates.
    .env(
        {
            "HF_XET_HIGH_PERFORMANCE": "1",
            # ADMITPERF_CONFIG must live in the image, not just the deploy
            # shell, or the container falls back to defaults.
            "ADMITPERF_CONFIG": _RAW_JSON,
            # vLLM picks FlashInfer for top-k/top-p sampling when it is
            # installed, and FlashInfer JIT-compiles that kernel on the first
            # sampled token — which needs nvcc, absent from this slim image.
            # The engine therefore died at the first request, not at startup.
            # Disabling it uses the PyTorch sampler instead, which is also the
            # better benchmark default: no compile stall partway through a run.
            "VLLM_USE_FLASHINFER_SAMPLER": "0",
        }
    )
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
# Without this a container serves ONE request at a time, so load queues at
# Modal's proxy and vLLM never sees concurrency: num_requests_running stays at
# 1, num_requests_waiting stays at 0, and the admission signal is flat while
# latency climbs. The queue has to form INSIDE vLLM for any of this to mean
# anything, so allow far more concurrent inputs than the engine will admit and
# let max_num_seqs be the only bottleneck.
@modal.concurrent(max_inputs=MAX_CONCURRENT_INPUTS)
@modal.web_server(port=VLLM_PORT, startup_timeout=STARTUP_TIMEOUT_S)
def serve() -> None:
    cmd = ["vllm", "serve", MODEL, *_vllm_args()]
    print("launching:", " ".join(cmd), flush=True)
    subprocess.Popen(" ".join(cmd), shell=True)
