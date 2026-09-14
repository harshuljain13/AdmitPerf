"""Modal provisioner — deploy the vLLM app and find out where it landed.

This shells out to the `modal` CLI rather than driving the SDK in-process.
That is deliberate: deploying is a slow, chatty, occasionally interactive
operation, and users already have `modal` configured and authenticated. Shelling
out means their existing login works and their existing `modal app logs` works
for debugging, which matters more here than API elegance.

Modal is the default provider because it creates the GPU itself. Lambda is the
other shape — you already own the box, and provisioning means installing vLLM on
it over ssh — and slots in behind the same two methods.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from admitperf.infra.session import Session

APP_PATH = Path(__file__).parent / "modal_app.py"
APP_NAME = "admitperf-vllm"

#: Modal prints the deployed web URL; grab the first https URL it mentions.
_URL = re.compile(r"https://[^\s│|]+\.modal\.run")


class ProvisionError(RuntimeError):
    """Provisioning failed. Message carries the provider's own output."""


@dataclass
class ModalSpec:
    model: str = "Qwen/Qwen3-0.6B"
    gpu: str = "A10G"
    served_model_name: str = "lab"
    #: Small on purpose: this is the bottleneck that creates queueing, and
    #: without queueing an admission policy has nothing to decide.
    max_num_seqs: int = 8
    max_model_len: int = 16384
    gpu_memory_utilization: float = 0.90


class ModalProvider:
    name = "modal"

    def __init__(self, spec: ModalSpec | None = None) -> None:
        self.spec = spec or ModalSpec()

    def preflight(self) -> None:
        """Fail before a slow deploy rather than during one."""
        if shutil.which("modal") is None:
            raise ProvisionError(
                "the `modal` CLI is not on PATH.\n    pip install 'admitperf[modal]' && modal setup"
            )

    def up(self, *, timeout_s: float = 1800.0) -> Session:
        self.preflight()
        env = {
            **os.environ,
            "ADMITPERF_MODEL": self.spec.model,
            "ADMITPERF_GPU": self.spec.gpu,
            "ADMITPERF_SERVED_NAME": self.spec.served_model_name,
            "ADMITPERF_MAX_NUM_SEQS": str(self.spec.max_num_seqs),
            "ADMITPERF_MAX_MODEL_LEN": str(self.spec.max_model_len),
            "ADMITPERF_GPU_MEM_UTIL": str(self.spec.gpu_memory_utilization),
        }

        proc = subprocess.run(
            ["modal", "deploy", str(APP_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        output = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise ProvisionError(f"`modal deploy` failed:\n{output}")

        match = _URL.search(output)
        if match is None:
            raise ProvisionError(
                "deploy succeeded but no .modal.run URL appeared in the output. "
                f"Check `modal app list`.\n{output}"
            )

        return Session(
            provider=self.name,
            engine="vllm",
            model=self.spec.model,
            endpoints=[match.group(0).rstrip("/")],
            gpu=self.spec.gpu,
            served_model_name=self.spec.served_model_name,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            handle={"app_name": APP_NAME},
        )

    def down(self, session: Session) -> None:
        self.preflight()
        app_name = session.handle.get("app_name", APP_NAME)
        proc = subprocess.run(
            ["modal", "app", "stop", app_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ProvisionError(f"`modal app stop {app_name}` failed:\n{proc.stderr}")


__all__ = ["APP_NAME", "ModalProvider", "ModalSpec", "ProvisionError"]
