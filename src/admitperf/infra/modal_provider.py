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
import sys
import time
from collections.abc import Callable
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


def _modal_bin() -> str | None:
    """The `modal` CLI, preferring the one beside the running interpreter.

    PATH alone is not enough. The dashboard launches these commands as
    subprocesses of whatever Python is running it, and a virtualenv that was
    never "activated" — `.venv/bin/python -m streamlit`, which is what `make
    dashboard` does — has `modal` sitting in its `bin/` while PATH knows
    nothing about it. Going by PATH turns a working install into "the modal CLI
    is not on PATH" two seconds into a run.
    """
    local = Path(sys.executable).parent / ("modal.exe" if os.name == "nt" else "modal")
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which("modal")


def _authenticated() -> bool:
    """Whether `modal setup` has been run, or tokens passed by environment."""
    return (Path.home() / ".modal.toml").exists() or bool(os.environ.get("MODAL_TOKEN_ID"))


class ModalProvider:
    name = "modal"

    def __init__(self, config: ExperimentConfig) -> None:
        self.config = config

    def preflight(self) -> None:
        """Fail before a slow deploy rather than during one."""
        if _modal_bin() is None:
            raise ProvisionError(
                "the `modal` CLI is not installed in this environment.\n"
                "    pip install 'admitperf[modal]' && modal setup"
            )
        if not _authenticated():
            # Distinguished from the above on purpose: "not installed" and
            # "installed but never logged in" have different fixes, and a
            # single message covering both sends you to reinstall something
            # that is already there.
            raise ProvisionError(
                "`modal` is installed but not authenticated — no ~/.modal.toml "
                "and no MODAL_TOKEN_ID.\n    modal setup"
            )
        # Catches a tensor-parallel size larger than the GPUs requested, which
        # would otherwise surface only after the weights had downloaded.
        self.config.infra.validate()

    def up(
        self,
        *,
        timeout_s: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> Session:
        """Deploy and return where it landed.

        A deploy takes minutes — image build, then weights — so the output is
        read line by line and handed to `on_line` as it arrives. Waiting for
        the process to exit before showing anything is the difference between
        "it is downloading a 1GB checkpoint" and a frozen screen.
        """
        self.preflight()
        infra = self.config.infra

        env = {
            **os.environ,
            "ADMITPERF_CONFIG": self.config.engine_env(),
            # Modal's CLI buffers when it is not writing to a terminal, which
            # would defeat the streaming above.
            "PYTHONUNBUFFERED": "1",
        }
        deadline = time.monotonic() + (timeout_s or (infra.startup_timeout_s + 600))

        proc = subprocess.Popen(
            [_modal_bin() or "modal", "deploy", str(APP_PATH)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            lines.append(line)
            if on_line is not None:
                on_line(line)
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise ProvisionError(
                    "`modal deploy` exceeded the startup timeout. The app may "
                    f"still be starting — check `modal app list`.\n{chr(10).join(lines)}"
                )

        returncode = proc.wait()
        output = "\n".join(lines)
        if returncode != 0:
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
        if _modal_bin() is None:
            raise ProvisionError("the `modal` CLI is not on PATH")
        app_name = session.handle.get("app_name", APP_NAME)
        proc = subprocess.run(
            # -y because there is no terminal here to confirm at. Without it
            # `modal app stop` aborts, teardown silently fails, and the
            # deployment keeps costing money.
            [_modal_bin() or "modal", "app", "stop", "-y", app_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ProvisionError(f"`modal app stop {app_name}` failed:\n{proc.stderr}")


__all__ = ["APP_NAME", "ModalProvider", "ProvisionError"]
