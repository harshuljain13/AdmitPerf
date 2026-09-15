"""Modal provisioner — deploy the vLLM app and find out where it landed.

Shells out to the `modal` CLI rather than driving the SDK in-process. Users
already have `modal` authenticated, and when a deploy misbehaves `modal app
logs` is where they will look; keeping that surface matters more here than API
tidiness.

Modal is the default provider because it creates the GPU itself. Lambda is the
other shape — you already own the box, and provisioning means installing vLLM
over ssh — and would slot in behind the same two methods.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from admitperf.core.config import ExperimentConfig
from admitperf.infra.session import Session

APP_PATH = Path(__file__).parent / "modal_app.py"
APP_NAME = "admitperf-vllm"

#: Modal prints the deployed web URL; grab the first https URL it mentions.
_URL = re.compile(r"https://[^\s│|]+\.modal\.run")


class ProvisionError(RuntimeError):
    """Provisioning failed. Message carries the provider's own output."""


class ModalProvider:
    name = "modal"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config

    def preflight(self) -> None:
        """Fail before a slow deploy rather than during one."""
        if shutil.which("modal") is None:
            raise ProvisionError(
                "the `modal` CLI is not on PATH.\n    pip install 'admitperf[modal]' && modal setup"
            )
        # Catches a tensor-parallel size larger than the GPUs requested, which
        # would otherwise surface only after the weights had downloaded.
        self.config.infra.validate()

    def up(self, *, timeout_s: float | None = None) -> Session:
        self.preflight()
        infra = self.config.infra

        env = {**os.environ, "ADMITPERF_CONFIG": self.config.engine_env()}

        proc = subprocess.run(
            ["modal", "deploy", str(APP_PATH)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s or (infra.startup_timeout_s + 600),
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
            model=infra.model,
            endpoints=[match.group(0).rstrip("/")],
            gpu=infra.modal_gpu,
            served_model_name=infra.served_model_name,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
            handle={"app_name": APP_NAME},
            config=self.config.to_dict(),
        )

    def down(self, session: Session) -> None:
        if shutil.which("modal") is None:
            raise ProvisionError("the `modal` CLI is not on PATH")
        app_name = session.handle.get("app_name", APP_NAME)
        proc = subprocess.run(
            ["modal", "app", "stop", app_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ProvisionError(f"`modal app stop {app_name}` failed:\n{proc.stderr}")


__all__ = ["APP_NAME", "ModalProvider", "ProvisionError"]
