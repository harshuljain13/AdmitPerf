"""The built-in policies.

Values here are grounded in a real run: Qwen2.5-0.5B on an A10G with
max_num_seqs=4 reached a queue 24 deep while kv_cache_usage_perc stayed under
0.005. That asymmetry is why both signals exist.
"""

from __future__ import annotations

import pytest

from admitperf.core.api import DecisionKind, Request, SystemState
from admitperf.core.registry import get_policy, requirements_of


def _state(*, kv: float | None = 0.0, waiting: int = 0, running: int = 0) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=kv,
        running_requests=running,
        waiting_requests=waiting,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )


def _req() -> Request:
    return Request(request_id="r1", tenant_id="t1", arrival_time=0.0, input_tokens=100)


# --- the three CONTRIBUTING tests, for every built-in ---------------------


@pytest.mark.parametrize("name", ["no_admission", "kv_threshold", "queue_depth"])
def test_admits_in_the_trivial_case(name: str) -> None:
    decision = get_policy(name).decide(_req(), _state())
    assert decision.kind is DecisionKind.ADMIT


@pytest.mark.parametrize("name", ["kv_threshold", "queue_depth"])
def test_rejects_at_the_boundary(name: str) -> None:
    state = _state(kv=0.99, waiting=100, running=4)
    decision = get_policy(name).decide(_req(), state)
    assert decision.kind is DecisionKind.REJECT
    assert decision.reason


@pytest.mark.parametrize("name", ["no_admission", "kv_threshold", "queue_depth"])
def test_decisions_are_deterministic(name: str) -> None:
    policy = get_policy(name)
    state = _state(kv=0.5, waiting=5)
    assert policy.decide(_req(), state) == policy.decide(_req(), state)


# --- queue depth ----------------------------------------------------------


def test_queue_depth_admits_below_and_rejects_above() -> None:
    policy = get_policy("queue_depth", max_waiting=8)
    assert policy.decide(_req(), _state(waiting=8)).kind is DecisionKind.ADMIT
    rejected = policy.decide(_req(), _state(waiting=9))
    assert rejected.kind is DecisionKind.REJECT
    assert rejected.reason == "queue_depth"


def test_queue_depth_declares_the_signal_it_reads() -> None:
    """Without this the policy would silently read a missing queue depth as
    zero and admit everything, which is how a benchmark measures nothing."""
    assert requirements_of(get_policy("queue_depth")) == frozenset({"waiting_requests"})


def test_queue_depth_ignores_kv_entirely() -> None:
    """The regime this policy is for: on a small model the KV cache never
    fills, so a KV-driven policy reads a flat line while the queue is deep."""
    policy = get_policy("queue_depth", max_waiting=2)
    assert policy.decide(_req(), _state(kv=0.001, waiting=20)).kind is DecisionKind.REJECT


def test_tighter_threshold_rejects_sooner() -> None:
    strict = get_policy("queue_depth", max_waiting=2)
    loose = get_policy("queue_depth", max_waiting=8)
    state = _state(waiting=5)
    assert strict.decide(_req(), state).kind is DecisionKind.REJECT
    assert loose.decide(_req(), state).kind is DecisionKind.ADMIT


def test_defer_variant_holds_instead_of_refusing() -> None:
    policy = get_policy("queue_depth_defer", max_waiting=2, retry_after_ms=100)
    decision = policy.decide(_req(), _state(waiting=20))
    assert decision.kind is DecisionKind.DEFER
    assert decision.retry_after_ms == 100


def test_negative_threshold_is_refused() -> None:
    with pytest.raises(ValueError, match="max_waiting"):
        get_policy("queue_depth", max_waiting=-1)


# --- kv threshold ---------------------------------------------------------


def test_kv_threshold_boundary_is_inclusive() -> None:
    policy = get_policy("kv_threshold", threshold=0.90)
    assert policy.decide(_req(), _state(kv=0.899)).kind is DecisionKind.ADMIT
    assert policy.decide(_req(), _state(kv=0.90)).kind is DecisionKind.REJECT


def test_kv_threshold_rejects_a_nonsense_setting() -> None:
    with pytest.raises(ValueError, match="threshold"):
        get_policy("kv_threshold", threshold=1.5)
