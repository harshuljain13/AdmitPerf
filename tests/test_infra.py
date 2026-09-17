"""Session file and the Modal provisioner.

The provisioner is tested by faking the `modal` CLI, because the thing worth
checking is that we recognise success, failure, and the URL — not that Modal
works.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from admitperf.core.config import ExperimentConfig
from admitperf.infra import modal_provider
from admitperf.infra.modal_provider import ModalProvider, ProvisionError
from admitperf.infra.session import Session, SessionStore

DEPLOY_OUTPUT = """
Building image...
Created objects.
├── 🔨 Created mount modal_app.py
└── 🔨 Created web function serve => https://harshul--admitperf-vllm-serve.modal.run
✓ App deployed in 42.1s! 🎉
"""


class _FakeDeploy:
    """Stands in for the `modal deploy` process, one line at a time."""

    def __init__(self, output: str, returncode: int = 0) -> None:
        self.stdout = iter(output.splitlines(keepends=True))
        self._returncode = returncode

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._returncode

    def kill(self) -> None:
        pass


def _stub_deploy(monkeypatch: pytest.MonkeyPatch, output: str, returncode: int = 0) -> None:
    """A modal CLI that is present, authenticated, and prints `output`."""
    monkeypatch.setattr(modal_provider, "_modal_bin", lambda: "/usr/bin/modal")
    monkeypatch.setattr(modal_provider, "_authenticated", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeDeploy(output, returncode))


def _session(tmp: Path) -> tuple[SessionStore, Session]:
    return SessionStore(tmp / "session.json"), Session(
        provider="modal",
        engine="vllm",
        model="Qwen/Qwen3-0.6B",
        endpoints=["https://example.modal.run"],
        gpu="A10G",
    )


def test_session_round_trips(tmp_path: Path) -> None:
    store, session = _session(tmp_path)
    store.save(session)
    assert store.load() == session


def test_primary_endpoint_is_the_first(tmp_path: Path) -> None:
    _, session = _session(tmp_path)
    assert session.primary == "https://example.modal.run"


def test_missing_session_says_what_to_do(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "nope.json")
    with pytest.raises(FileNotFoundError, match="infra up"):
        store.load()


def test_clear_removes_the_file(tmp_path: Path) -> None:
    store, session = _session(tmp_path)
    store.save(session)
    store.clear()
    assert not store.exists()


def test_deploy_output_yields_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_deploy(monkeypatch, DEPLOY_OUTPUT)

    session = ModalProvider(ExperimentConfig()).up()

    assert session.endpoints == ["https://harshul--admitperf-vllm-serve.modal.run"]
    assert session.provider == "modal"
    assert session.created_at


def test_deploy_output_is_streamed_while_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deploy takes minutes; the caller needs the lines before the exit code."""
    _stub_deploy(monkeypatch, DEPLOY_OUTPUT)

    seen: list[str] = []
    ModalProvider(ExperimentConfig()).up(on_line=seen.append)

    assert "Building image..." in seen
    assert any("App deployed" in line for line in seen)
    assert all(not line.endswith("\n") for line in seen)


def test_missing_modal_cli_fails_before_a_slow_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(modal_provider, "_modal_bin", lambda: None)
    with pytest.raises(ProvisionError, match="not installed"):
        ModalProvider(ExperimentConfig()).up()


def test_installed_but_unauthenticated_says_so_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Not installed" and "never logged in" have different fixes. One message
    covering both sends you to reinstall something that is already there."""
    monkeypatch.setattr(modal_provider, "_modal_bin", lambda: "/usr/bin/modal")
    monkeypatch.setattr(modal_provider, "_authenticated", lambda: False)
    with pytest.raises(ProvisionError, match="not authenticated"):
        ModalProvider(ExperimentConfig()).up()


def test_modal_is_found_beside_the_interpreter_when_not_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`make dashboard` runs `.venv/bin/python` without activating the venv, so
    PATH knows nothing about `.venv/bin/modal`. Going by PATH alone turned a
    working install into a failure two seconds into a run."""
    binary = tmp_path / "modal"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(modal_provider.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(modal_provider.shutil, "which", lambda _: None)

    assert modal_provider._modal_bin() == str(binary)


def test_the_deploy_uses_the_resolved_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolving it and then invoking bare `modal` would reintroduce the bug."""
    seen: list[list[str]] = []
    monkeypatch.setattr(modal_provider, "_modal_bin", lambda: "/somewhere/else/modal")
    monkeypatch.setattr(modal_provider, "_authenticated", lambda: True)

    def record(cmd, **kwargs):
        seen.append(cmd)
        return _FakeDeploy(DEPLOY_OUTPUT)

    monkeypatch.setattr(subprocess, "Popen", record)
    ModalProvider(ExperimentConfig()).up()

    assert seen[0][0] == "/somewhere/else/modal"


def test_deploy_failure_surfaces_modal_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_deploy(monkeypatch, "no such GPU: H200x\n", returncode=1)
    with pytest.raises(ProvisionError, match="no such GPU"):
        ModalProvider(ExperimentConfig()).up()


def test_deploy_without_a_url_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent success with no endpoint would strand `run` later."""
    _stub_deploy(monkeypatch, "deployed, somewhere\n")
    with pytest.raises(ProvisionError, match="no .modal.run URL"):
        ModalProvider(ExperimentConfig()).up()
