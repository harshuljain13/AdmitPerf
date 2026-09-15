"""The policy that wires estimator and maths together.

What separates it from the threshold policies: it reads the *request*, not just
the fleet. Same state, different deadline, different answer.
"""

from __future__ import annotations

import pytest

from admitperf.core.api import DecisionKind, Request, SystemState
from admitperf.core.registry import get_policy, requirements_of

BUSY = {
    "vllm:request_prefill_time_seconds_sum": 1.0,
    "vllm:request_prefill_time_seconds_count": 10.0,
    "vllm:request_prefill_kv_computed_tokens_sum": 8000.0,
    "vllm:request_prefill_kv_computed_tokens_count": 10.0,
    "vllm:inter_token_latency_seconds_sum": 2.0,
    "vllm:inter_token_latency_seconds_count": 100.0,
    "vllm:request_decode_time_seconds_sum": 20.0,
    "vllm:request_decode_time_seconds_count": 10.0,
}


def _state(*, running: int = 4, waiting: int = 0, metrics: dict | None = None) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=0.1,
        running_requests=running,
        waiting_requests=waiting,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics=BUSY if metrics is None else metrics,
    )


def _req(*, ttft: float | None = 500.0, tokens: int = 800) -> Request:
    return Request(
        request_id="r1",
        tenant_id="t1",
        arrival_time=0.0,
        input_tokens=tokens,
        expected_output_tokens=100,
        deadline_ttft_ms=ttft,
    )


def _policy(**kw):
    return get_policy("chronos_inspired", **kw)


def test_it_is_registered_and_declares_its_signals() -> None:
    assert requirements_of(_policy()) == frozenset({"running_requests", "waiting_requests"})


def test_admits_on_an_idle_fleet() -> None:
    assert _policy().decide(_req(), _state(running=0)).kind is DecisionKind.ADMIT


def test_rejects_when_the_queue_makes_the_deadline_unreachable() -> None:
    decision = _policy(concurrency=4).decide(_req(ttft=200.0), _state(running=4, waiting=60))
    assert decision.kind is DecisionKind.REJECT
    assert decision.reason == "deadline_unmeetable"


def test_same_fleet_state_different_deadline_different_answer() -> None:
    """The behaviour no threshold policy can reproduce."""
    policy = _policy(concurrency=4)
    state = _state(running=4, waiting=60)

    assert policy.decide(_req(ttft=200.0), state).kind is DecisionKind.REJECT
    assert policy.decide(_req(ttft=120_000.0), state).kind is DecisionKind.ADMIT


def test_a_request_without_a_deadline_is_admitted() -> None:
    """A deadline-aware policy has no opinion about a request that states no
    deadline; refusing it would be a threshold policy wearing a disguise."""
    decision = _policy().decide(_req(ttft=None), _state(running=4, waiting=100))
    assert decision.kind is DecisionKind.ADMIT


def test_admits_while_uncalibrated_and_counts_it() -> None:
    """Before anything has completed there is no basis to predict. Refusing
    would shed the traffic needed to calibrate and never recover — but the
    count is kept, because "admitted everything while blind" and "judged
    everything admissible" must be distinguishable afterwards."""
    policy = _policy()
    decision = policy.decide(_req(), _state(metrics={}))

    assert decision.kind is DecisionKind.ADMIT
    assert policy.decisions_uncalibrated == 1
    assert policy.decisions_predicted == 0


def test_predicted_decisions_are_counted_separately() -> None:
    policy = _policy()
    policy.decide(_req(), _state(running=0))
    assert policy.decisions_predicted == 1


def test_defer_variant_holds_instead_of_refusing() -> None:
    policy = _policy(concurrency=4, defer_instead_of_reject=True, retry_after_ms=300)
    decision = policy.decide(_req(ttft=200.0), _state(running=4, waiting=60))

    assert decision.kind is DecisionKind.DEFER
    assert decision.retry_after_ms == 300


def test_safety_factor_makes_it_shed_sooner() -> None:
    # One batch of waiting: ~2.1s predicted, comfortably inside 5s.
    state = _state(running=4, waiting=0)
    lenient = _policy(concurrency=4, safety_factor=1.0).decide(_req(ttft=5000.0), state)
    strict = _policy(concurrency=4, safety_factor=10.0).decide(_req(ttft=5000.0), state)

    assert lenient.kind is DecisionKind.ADMIT
    assert strict.kind is DecisionKind.REJECT


def test_decisions_are_deterministic() -> None:
    policy = _policy()
    state = _state(running=4, waiting=10)
    assert policy.decide(_req(), state) == policy.decide(_req(), state)


@pytest.mark.parametrize("kwargs", [{"concurrency": 0}, {"safety_factor": 0}])
def test_nonsense_configuration_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        _policy(**kwargs)


def test_the_name_says_inspired() -> None:
    """Fidelity rule: this is not a verified port of the paper, and the class
    name is the only place a reader is guaranteed to look."""
    assert "inspired" in type(_policy()).__name__.lower()
    assert "inspired" in _policy().name
