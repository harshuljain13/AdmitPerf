"""A Modal app that serves vLLM behind an HTTPS URL.

Deployed by `admitperf infra up --provider modal`. Configuration arrives as
environment variables rather than function arguments, because Modal builds this
module at deploy time and the values must be known then.

The vLLM flags here are not arbitrary — they come from a working two-replica
Lambda setup (module7-admission-and-routing/setup/launch_replicas.sh):

  --max-num-seqs is kept deliberately small. It is the bottleneck that creates
  queueing, and without queueing there is nothing for an admission policy to
  decide about. A generous limit produces a benchmark where every policy looks
  identical because the fleet never saturates.

  --served-model-name fixes the name clients use, so swapping the underlying
  model does not change the request payload.
"""

from __future__ import annotations

import os
import subprocess

MODEL = os.environ.get("ADMITPERF_MODEL", "Qwen/Qwen3-0.6B")
SERVED_NAME = os.environ.get("ADMITPERF_SERVED_NAME", "lab")
GPU = os.environ.get("ADMITPERF_GPU", "A10G")
MAX_NUM_SEQS = os.environ.get("ADMITPERF_MAX_NUM_SEQS", "8")
MAX_MODEL_LEN = os.environ.get("ADMITPERF_MAX_MODEL_LEN", "16384")
GPU_MEM_UTIL = os.environ.get("ADMITPERF_GPU_MEM_UTIL", "0.90")

# Gated weights (Llama, Gemma) need a HuggingFace token in the container. Two
# ways to provide one, and none is required for ungated models:
#
#   HF_TOKEN=hf_...              in your shell or .env — forwarded at deploy
#   ADMITPERF_HF_SECRET=name     an existing Modal secret, by name
#
# Attaching nothing by default is deliberate: requiring a secret that does not
# exist would fail the deploy for everyone who never needed one.
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
HF_SECRET = os.environ.get("ADMITPERF_HF_SECRET", "").strip()
VLLM_PORT = 8000

try:
    import modal
except ImportError as exc:  # pragma: no cover - only meaningful with modal installed
    raise SystemExit(
        "modal is not installed. Install the provisioning extra:\n"
        "    pip install 'admitperf[modal]'"
    ) from exc

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm>=0.8", "huggingface_hub[hf_transfer]")
    # Weights are the slow part of a cold start; the fast downloader pays for
    # itself on every deploy.
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1", "VLLM_USE_V1": "1"})
)

app = modal.App("admitperf-vllm")

# Cache weights across deploys, so bringing the same model back up is quick.
hf_cache = modal.Volume.from_name("admitperf-hf-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    volumes={"/root/.cache/huggingface": hf_cache},
    # from_dict creates the secret at deploy time. The token is never written
    # into an image layer, so it does not end up in the cached build.
    secrets=(
        [modal.Secret.from_dict({"HF_TOKEN": HF_TOKEN})]
        if HF_TOKEN
        else [modal.Secret.from_name(HF_SECRET)]
        if HF_SECRET
        else []
    ),
    timeout=60 * 60,
    # One container. Modal would otherwise autoscale, and a fleet that grows
    # under load is measuring elasticity rather than admission control.
    max_containers=1,
    scaledown_window=60 * 5,
)
@modal.web_server(port=VLLM_PORT, startup_timeout=60 * 15)
def serve() -> None:
    cmd = [
        "vllm",
        "serve",
        MODEL,
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        "--served-model-name",
        SERVED_NAME,
        "--max-num-seqs",
        MAX_NUM_SEQS,
        "--max-model-len",
        MAX_MODEL_LEN,
        "--gpu-memory-utilization",
        GPU_MEM_UTIL,
        "--scheduling-policy",
        "priority",
        # /metrics is the whole point; make sure it is not disabled.
        "--disable-log-requests",
    ]
    subprocess.Popen(" ".join(cmd), shell=True)
