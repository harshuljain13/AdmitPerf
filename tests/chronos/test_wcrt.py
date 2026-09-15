"""The response-time arithmetic.

Pure functions, so these tests need no engine, no mock and no clock — which is
the reason the maths lives in its own module. The numbers here are hand-checked
against the model in the docstring.
"""

from __future__ import annotations

import pytest

from admitperf.policies.chronos.estimator import ServiceRates
from admitperf.policies.chronos.wcrt import predict, queue_delay_seconds

# 8000 tokens/s prefill, 20ms per output token, 2s mean decode.
RATES = ServiceRates(
    prefill_tokens_per_s=8000.0,
    seconds_per_output_token=0.020,
    mean_decode_seconds=2.0,
    samples=10,
)


def _predict(**kw):
    base = dict(
        input_tokens=800,
        expected_output_tokens=100,
        running=0,
        waiting=0,
        concurrency=4,
        rates=RATES,
        deadline_ttft_ms=None,
        deadline_tbt_ms=None,
    )
    return predict(**{**base, **kw})


# --- queue delay ----------------------------------------------------------


def test_no_queue_means_no_waiting() -> None:
    assert queue_delay_seconds(queued=0, concurrency=4, service_seconds=2.0) == 0.0


def test_slots_free_one_at_a_time_not_in_lockstep() -> None:
    """Four slots each held for 2s means a departure every 0.5s, so the first
    request in line waits 0.5s — not the 2s a whole-batch model would charge.
    That overestimate rejected traffic the fleet could comfortably serve."""
    assert queue_delay_seconds(queued=1, concurrency=4, service_seconds=2.0) == pytest.approx(0.5)
    assert queue_delay_seconds(queued=4, concurrency=4, service_seconds=2.0) == pytest.approx(2.0)


def test_delay_grows_linearly_with_the_queue() -> None:
    a = queue_delay_seconds(queued=10, concurrency=4, service_seconds=2.0)
    b = queue_delay_seconds(queued=20, concurrency=4, service_seconds=2.0)
    assert b == pytest.approx(a * 2)


def test_more_slots_drain_faster() -> None:
    narrow = queue_delay_seconds(queued=16, concurrency=4, service_seconds=2.0)
    wide = queue_delay_seconds(queued=16, concurrency=16, service_seconds=2.0)
    assert wide == pytest.approx(narrow / 4)


def test_zero_concurrency_is_refused() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        queue_delay_seconds(queued=1, concurrency=0, service_seconds=1.0)


# --- prediction -----------------------------------------------------------


def test_idle_fleet_costs_only_our_own_prefill() -> None:
    """800 tokens at 8000 tok/s = 100ms, and nothing to wait behind."""
    p = _predict(running=0, waiting=0)
    assert p.queue_delay_ms == 0.0
    assert p.own_prefill_ms == pytest.approx(100.0)
    assert p.ttft_ms == pytest.approx(100.0)


def test_a_full_fleet_costs_one_departure_of_waiting() -> None:
    """All four slots busy and nothing queued: we wait for the next single
    departure, not for the whole batch to turn over."""
    p = _predict(running=4, waiting=0)
    assert p.queue_delay_ms > 0
    assert p.ttft_ms > p.own_prefill_ms
    # 1 ahead / 4 slots x ~2.016s service = ~504ms, not ~2016ms.
    assert p.queue_delay_ms == pytest.approx(504.0, rel=0.05)


def test_deeper_queues_predict_longer_waits() -> None:
    shallow = _predict(running=4, waiting=2).ttft_ms
    deep = _predict(running=4, waiting=40).ttft_ms
    assert deep > shallow


def test_free_slots_absorb_part_of_the_queue() -> None:
    """Two idle slots means a short queue does not have to wait at all."""
    assert _predict(running=2, waiting=0, concurrency=4).queue_delay_ms == 0.0


def test_longer_prompts_cost_more_prefill() -> None:
    short = _predict(input_tokens=800).own_prefill_ms
    long = _predict(input_tokens=8000).own_prefill_ms
    assert long == pytest.approx(short * 10)


# --- deadline verdicts ----------------------------------------------------


def test_generous_deadline_fits_and_tight_one_does_not() -> None:
    assert _predict(deadline_ttft_ms=60_000.0).fits
    assert not _predict(running=4, waiting=40, deadline_ttft_ms=50.0).fits


def test_the_violated_deadline_is_named() -> None:
    """The reason reaches the results bundle, so a reader can tell a
    first-token miss from a streaming-smoothness miss."""
    p = _predict(running=4, waiting=40, deadline_ttft_ms=1.0)
    assert p.violated == "ttft"

    tbt = _predict(deadline_tbt_ms=1.0)  # measured 20ms/token cannot meet 1ms
    assert tbt.violated == "tbt"


def test_a_request_with_no_deadline_is_never_judged_to_miss() -> None:
    p = _predict(running=4, waiting=100)
    assert p.ttft_fits is None and p.tbt_fits is None
    assert p.fits


def test_two_requests_in_the_same_state_can_get_different_verdicts() -> None:
    """The point of a deadline-aware policy: the fleet state is identical, so a
    threshold policy would treat these the same."""
    interactive = _predict(running=4, waiting=20, deadline_ttft_ms=500.0)
    batch = _predict(running=4, waiting=20, deadline_ttft_ms=30_000.0)

    assert not interactive.fits
    assert batch.fits


def test_safety_factor_sheds_earlier() -> None:
    """Above 1.0 the policy is pessimistic, which is the knob for a mean-based
    model that understates the tail."""
    optimistic = _predict(running=4, waiting=10, safety_factor=1.0).ttft_ms
    pessimistic = _predict(running=4, waiting=10, safety_factor=2.0).ttft_ms
    assert pessimistic == pytest.approx(optimistic * 2)


def test_predicting_without_rates_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="calibrated"):
        _predict(rates=ServiceRates())
