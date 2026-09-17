"""Waiting for an engine that is still starting.

`infra up` returns when Modal prints the URL, which is minutes before the
engine can serve: the container is created on the first request and then has to
load weights. Checking once and reporting FAIL describes the clock rather than
the deployment — which is exactly what it did the first time a real deploy got
this far.
"""

from __future__ import annotations

import asyncio

import pytest

from admitperf.cli import _startup_budget, wait_for_engine


class _Engine:
    """Healthy only after `after` calls, like a container loading weights."""

    def __init__(self, after: int, raises: bool = False) -> None:
        self.after = after
        self.calls = 0
        self.raises = raises

    async def health(self) -> bool:
        self.calls += 1
        if self.calls <= self.after:
            if self.raises:
                raise ConnectionError("connection refused")
            return False
        return True


def test_it_waits_rather_than_failing_on_the_first_try() -> None:
    engine = _Engine(after=3)
    assert asyncio.run(wait_for_engine(engine, timeout_s=30, initial_delay=0.001)) is True
    assert engine.calls == 4


def test_a_refused_connection_is_starting_not_failing() -> None:
    """Nothing is listening yet while the container is being created."""
    engine = _Engine(after=2, raises=True)
    assert asyncio.run(wait_for_engine(engine, timeout_s=30, initial_delay=0.001)) is True


def test_it_gives_up_at_the_budget() -> None:
    engine = _Engine(after=10_000)
    assert asyncio.run(wait_for_engine(engine, timeout_s=0.2, initial_delay=0.01)) is False


def test_progress_is_reported_while_waiting() -> None:
    """A silent wait and a hung deploy look identical."""
    seen: list[float] = []
    engine = _Engine(after=2)
    asyncio.run(wait_for_engine(engine, timeout_s=30, tick=seen.append, initial_delay=0.001))
    assert seen, "no progress was reported"


def test_your_own_engine_is_not_waited_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """An engine you started yourself is either up or it is not."""
    assert _startup_budget("http://127.0.0.1:8000") == 30.0


def test_the_budget_comes_from_the_deployment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from admitperf.infra import session as session_mod
    from admitperf.infra.session import Session, SessionStore

    store = SessionStore(tmp_path / "session.json")
    store.save(
        Session(
            provider="modal",
            engine="vllm",
            model="m",
            endpoints=["https://x.modal.run"],
            config={"infra": {"startup_timeout_s": 1234}},
        )
    )
    monkeypatch.setattr(session_mod, "SESSION_FILE", store.path)
    monkeypatch.setattr(
        SessionStore, "__init__", lambda self, path=store.path: setattr(self, "path", store.path)
    )

    assert _startup_budget(None) == 1234.0
