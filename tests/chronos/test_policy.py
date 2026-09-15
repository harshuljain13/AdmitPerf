"""Algorithm 1 wired to a live engine's telemetry."""

from __future__ import annotations

import pytest

from admitperf.core.api import DecisionKind, Request, SystemState
from admitperf.core.registry import get_policy, requirements_of
from admitperf.policies.chronos.estimator import ArrivalRateWindow

CALIBRATED = {
    "vllm:request_prefill_time_seconds_sum": 1.0,
    "vllm:request_prefill_time_seconds_count": 10.0,
    "vllm:request_prefill_kv_computed_tokens_sum": 8000.0,
    "vllm:request_prefill_kv_computed_tokens_count": 10.0,
    "vllm:inter_token_latency_seconds_sum": 2.0,
    "vllm:inter_token_latency_seconds_count": 100.0,
    "vllm:request_decode_time_seconds_sum": 20.0,
    "vllm:request_decode_time_seconds_count": 10.0,
}


def _state(*, running: int = 2, waiting: int = 0, metrics: dict | None = None) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=0.05,
        running_requests=running,
        waiting_requests=waiting,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics=CALIBRATED if metrics is None else metrics,
    )


def _req(*, ttft: float | None = 2000.0, tbt: float | None = 200.0, at: float = 0.0, tokens: int = 512):
    return Request(
        request_id=f"r{at}",
        tenant_id="t1",
        arrival_time=at,
        input_tokens=tokens,
        expected_output_tokens=128,
        deadline_ttft_ms=ttft,
        deadline_tbt_ms=tbt,
    )


def _policy(**kw):
    return get_policy("chronos_inspired", **kw)


# --- arrival rate window --------------------------------------------------


def test_window_measures_arrival_rate() -> None:
    w = ArrivalRateWindow(window_s=60.0, bucket_s=1.0)
    for i in range(10):
        w.record(now=100.0 + i * 0.1)  # ten arrivals inside one second
    assert w.peak_rate_hz(now=100.5) == pytest.approx(10.0)


def test_window_forgets_old_arrivals() -> None:
    w = ArrivalRateWindow(window_s=10.0, bucket_s=1.0)
    for i in range(5):
        w.record(now=100.0 + i * 0.1)
    assert w.peak_rate_hz(now=100.5) > 0
    assert w.peak_rate_hz(now=200.0) == 0.0


def test_peak_catches_a_burst_the_mean_would_hide() -> None:
    """Algorithm 1 uses lambda_max for a reason: averaging a burst over 60s
    hides exactly the overload the utilization test exists to catch."""
    w = ArrivalRateWindow(window_s=60.0, bucket_s=1.0)
    for i in range(50):
        w.record(now=100.0 + i * 0.01)  # 50 arrivals in one bucket
    w.record(now=140.0)  # then near-silence

    assert w.peak_rate_hz(now=141.0) == pytest.approx(50.0)
    assert w.mean_rate_hz(now=141.0) < 5.0


# --- policy ---------------------------------------------------------------


def test_registered_and_declares_its_signals() -> None:
    assert requirements_of(_policy()) == frozenset({"running_requests", "waiting_requests"})


def test_admits_under_light_load() -> None:
    assert _policy().decide(_req(), _state()).kind is DecisionKind.ADMIT


def test_rejects_once_utilization_makes_the_deadline_infeasible() -> None:
    policy = _policy()
    # Drive a burst through the window, then ask.
    for i in range(200):
        policy.decide(_req(at=1000.0 + i * 0.002), _state())
    decision = policy.decide(_req(at=1000.4), _state())

    assert decision.kind is DecisionKind.REJECT
    assert decision.reason in {"overloaded", "deadline_unmeetable"}


def test_admits_while_uncalibrated_and_counts_it_separately() -> None:
    """"Admitted everything while blind" and "found everything feasible" must
    be distinguishable afterwards, or a run cannot be interpreted."""
    policy = _policy()
    assert policy.decide(_req(), _state(metrics={})).kind is DecisionKind.ADMIT
    assert policy.counts["uncalibrated"] == 1
    assert policy.counts["admitted"] == 0


def test_a_request_without_deadlines_is_admitted() -> None:
    """A feasibility test needs something to be feasible against."""
    policy = _policy()
    assert policy.decide(_req(ttft=None, tbt=None), _state()).kind is DecisionKind.ADMIT
    assert policy.counts["no_deadline"] == 1


def test_per_check_tallies_explain_a_run() -> None:
    policy = _policy()
    policy.decide(_req(), _state())
    assert sum(policy.counts.values()) == 1
    assert policy.counts["admitted"] == 1


def test_defer_variant_holds_instead_of_refusing() -> None:
    policy = _policy(defer_instead_of_reject=True, retry_after_ms=300)
    for i in range(200):
        policy.decide(_req(at=2000.0 + i * 0.002), _state())
    decision = policy.decide(_req(at=2000.4), _state())
    assert decision.kind is DecisionKind.DEFER
    assert decision.retry_after_ms == 300


def test_fixed_parameters_bypass_telemetry_fitting() -> None:
    """Lets the paper's roofline values be used directly, which is what a
    fidelity comparison against the published simulator needs."""
    policy = _policy(fit_from_telemetry=False, alpha_ms_per_token=0.080, beta_ms_per_token=0.250)
    assert policy.decide(_req(), _state(metrics={})).kind is DecisionKind.ADMIT
    assert policy.counts["uncalibrated"] == 0


def test_decisions_are_deterministic() -> None:
    a, b = _policy(), _policy()
    assert a.decide(_req(), _state()) == b.decide(_req(), _state())


@pytest.mark.parametrize("kwargs", [{"safety_factor": 0}, {"chunk_tokens": 0}])
def test_nonsense_configuration_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        _policy(**kwargs)


def test_the_name_says_inspired() -> None:
    """Fidelity rule: this runs the paper's algorithm on a different substrate
    with fitted rather than derived parameters."""
    assert "inspired" in _policy().name
