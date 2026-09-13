"""Task 2.3 — the defer queue."""

from __future__ import annotations

import pytest

from admitperf.core.api import Request
from admitperf.core.deferq import FifoDeferQueue
from admitperf.core.ports import DeferQueue


def _req(rid: str) -> Request:
    return Request(request_id=rid, tenant_id="t1", arrival_time=0.0, input_tokens=10)


def test_satisfies_the_port() -> None:
    assert isinstance(FifoDeferQueue(), DeferQueue)


def test_only_due_requests_come_back() -> None:
    q = FifoDeferQueue()
    q.push(_req("a"), retry_at=1.0, attempt=1)
    q.push(_req("b"), retry_at=5.0, attempt=1)

    assert [r.request_id for r, _ in q.due(now=1.0)] == ["a"]
    assert len(q) == 1
    assert [r.request_id for r, _ in q.due(now=5.0)] == ["b"]
    assert len(q) == 0


def test_ties_break_by_insertion_order() -> None:
    """Determinism: identical due times must pop in a stable sequence, or two
    runs of the same trace diverge."""
    q = FifoDeferQueue()
    for rid in ("a", "b", "c"):
        q.push(_req(rid), retry_at=2.0, attempt=1)

    assert [r.request_id for r, _ in q.due(now=2.0)] == ["a", "b", "c"]


def test_attempt_count_survives_the_round_trip() -> None:
    q = FifoDeferQueue()
    q.push(_req("a"), retry_at=0.0, attempt=7)
    ((_, attempt),) = q.due(now=0.0)
    assert attempt == 7


def test_exhaustion_stops_an_endless_defer_loop() -> None:
    """A policy that defers under pressure, and stays under pressure, would
    otherwise hold a request forever while the run looks healthy."""
    q = FifoDeferQueue(max_defers=100)
    assert not q.exhausted(99)
    assert q.exhausted(100)


def test_next_due_at_lets_the_runner_sleep_instead_of_poll() -> None:
    q = FifoDeferQueue()
    assert q.next_due_at() is None
    q.push(_req("a"), retry_at=3.0, attempt=1)
    q.push(_req("b"), retry_at=1.5, attempt=1)
    assert q.next_due_at() == 1.5


def test_drain_returns_everything_in_due_order() -> None:
    q = FifoDeferQueue()
    q.push(_req("late"), retry_at=9.0, attempt=1)
    q.push(_req("early"), retry_at=1.0, attempt=1)

    assert [r.request_id for r, _ in q.drain()] == ["early", "late"]
    assert len(q) == 0


def test_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError, match="max_defers"):
        FifoDeferQueue(max_defers=0)
