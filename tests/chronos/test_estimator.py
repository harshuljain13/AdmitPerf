"""Service-rate estimation from engine counters.

The property that matters: rates describe the *recent* window, not the server's
lifetime. A lifetime mean is dominated by the cold, uncontended start of a run
and would make a predictive policy optimistic exactly when the fleet is busy.
"""

from __future__ import annotations

import pytest

from admitperf.core.api import SystemState
from admitperf.policies.chronos.estimator import ServiceRateEstimator, ServiceRates


def _state(**metrics: float) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=0.1,
        running_requests=1,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics=dict(metrics),
    )


def _counters(*, prefill_s: float, prefill_tokens: float, reqs: float, itl_s: float, itl_n: float):
    return {
        "vllm:request_prefill_time_seconds_sum": prefill_s,
        "vllm:request_prefill_time_seconds_count": reqs,
        "vllm:request_prefill_kv_computed_tokens_sum": prefill_tokens,
        "vllm:request_prefill_kv_computed_tokens_count": reqs,
        "vllm:inter_token_latency_seconds_sum": itl_s,
        "vllm:inter_token_latency_seconds_count": itl_n,
    }


def test_nothing_served_yet_is_uncalibrated() -> None:
    """A fleet with no completions cannot be characterised, and saying so is
    the point: a guessed rate makes a predictive policy confidently wrong."""
    rates = ServiceRateEstimator().update(_state())
    assert not rates.is_calibrated
    assert rates.prefill_tokens_per_s is None


def test_first_scrape_uses_the_lifetime_mean() -> None:
    """There is no window to difference against yet, and waiting for a second
    completion would leave the policy blind for most of a short run."""
    est = ServiceRateEstimator()
    rates = est.update(_state(**_counters(prefill_s=1.0, prefill_tokens=8000, reqs=10, itl_s=2.0, itl_n=100)))

    assert rates.is_calibrated
    assert rates.prefill_tokens_per_s == pytest.approx(8000.0)
    assert rates.seconds_per_output_token == pytest.approx(0.02)


def test_rates_reflect_the_recent_window_not_the_lifetime() -> None:
    """The headline behaviour. A fast start followed by a slow, contended
    window must report the slow rate."""
    est = ServiceRateEstimator()
    # Fast start: 10k tokens/s.
    est.update(_state(**_counters(prefill_s=1.0, prefill_tokens=10_000, reqs=10, itl_s=1.0, itl_n=100)))
    # Next window is 5x slower: 1000 more tokens took 1 more second.
    rates = est.update(
        _state(**_counters(prefill_s=2.0, prefill_tokens=11_000, reqs=20, itl_s=3.0, itl_n=150))
    )

    assert rates.prefill_tokens_per_s == pytest.approx(1000.0)
    # Lifetime mean would have been 11000/2 = 5500, five times optimistic.
    assert rates.prefill_tokens_per_s < 5500


def test_empty_window_keeps_the_last_known_rates() -> None:
    """Most scrapes land between completions — at a 100ms interval against
    multi-second requests, nearly all of them. Reporting uncalibrated then
    would make the policy flicker on and off."""
    est = ServiceRateEstimator()
    first = _counters(prefill_s=1.0, prefill_tokens=8000, reqs=10, itl_s=2.0, itl_n=100)
    est.update(_state(**first))
    before = est.update(_state(**_counters(prefill_s=2.0, prefill_tokens=16000, reqs=20, itl_s=4.0, itl_n=200)))

    unchanged = est.update(_state(**_counters(prefill_s=2.0, prefill_tokens=16000, reqs=20, itl_s=4.0, itl_n=200)))

    assert unchanged == before
    assert unchanged.is_calibrated


def test_engine_restart_does_not_produce_a_negative_rate() -> None:
    """Counters reset to zero on restart. Without a guard the delta goes
    negative and every later estimate is poisoned."""
    est = ServiceRateEstimator()
    est.update(_state(**_counters(prefill_s=10.0, prefill_tokens=80_000, reqs=100, itl_s=20.0, itl_n=1000)))
    after_restart = est.update(_state(**_counters(prefill_s=0.1, prefill_tokens=800, reqs=1, itl_s=0.2, itl_n=10)))

    assert after_restart.prefill_tokens_per_s is None or after_restart.prefill_tokens_per_s > 0


def test_missing_counters_are_not_invented() -> None:
    rates = ServiceRateEstimator().update(_state(**{"vllm:num_requests_running": 3.0}))
    assert rates.seconds_per_output_token is None
    assert not rates.is_calibrated


def test_rejects_a_nonsense_threshold() -> None:
    with pytest.raises(ValueError, match="min_samples"):
        ServiceRateEstimator(min_samples=0)


def test_calibration_requires_both_rates() -> None:
    assert not ServiceRates(prefill_tokens_per_s=8000.0).is_calibrated
    assert not ServiceRates(seconds_per_output_token=0.02).is_calibrated
    assert ServiceRates(prefill_tokens_per_s=8000.0, seconds_per_output_token=0.02).is_calibrated
