"""Session file and the Modal provisioner.

The provisioner is tested by faking the `modal` CLI, because the thing worth
checking is that we recognise success, failure, and the URL — not that Modal
works.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from admitperf.infra.modal_provider import ModalProvider, ModalSpec, ProvisionError
from admitperf.infra.session import Session, SessionStore

DEPLOY_OUTPUT = """
Building image...
Created objects.
├── 🔨 Created mount modal_app.py
└── 🔨 Created web function serve => https://harshul--admitperf-vllm-serve.modal.run
✓ App deployed in 42.1s! 🎉
"""


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
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/modal")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, DEPLOY_OUTPUT, ""),
    )

    session = ModalProvider(ModalSpec(model="Qwen/Qwen3-0.6B")).up()

    assert session.endpoints == ["https://harshul--admitperf-vllm-serve.modal.run"]
    assert session.provider == "modal"
    assert session.created_at


def test_missing_modal_cli_fails_before_a_slow_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ProvisionError, match="modal setup"):
        ModalProvider().up()


def test_deploy_failure_surfaces_modal_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/modal")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no such GPU: H200x"),
    )
    with pytest.raises(ProvisionError, match="no such GPU"):
        ModalProvider().up()


def test_deploy_without_a_url_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A silent success with no endpoint would strand `run` later."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/modal")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "deployed, somewhere", ""),
    )
    with pytest.raises(ProvisionError, match="no .modal.run URL"):
        ModalProvider().up()
