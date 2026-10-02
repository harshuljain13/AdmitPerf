"""The cached snapshot.

Two properties worth pinning: reads do no I/O, and a cache that has never been
filled reports "no signal" rather than a confident zero.
"""

from __future__ import annotations

import math

from admitperf.core.api import SystemState
from admitperf.core.ports import STATE_AGE_KEY
from admitperf.core.state import StateCache, state_age


class FakeTime:
    """Hand-cranked clock, so age can be tested without sleeping."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _state(kv: float) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=kv,
        running_requests=3,
        waiting_requests=2,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={"vllm:num_preemptions_total": 1.0},
    )


def test_before_any_scrape_there_is_no_signal_not_a_zero() -> None:
    """A policy reading kv_used_fraction as 0.0 would decide the cache is
    empty and admit everything, at the point we know least about the fleet."""
    cache = StateCache()
    state = cache.current()

    assert state.kv_used_fraction is None
    assert state_age(state) == math.inf
    assert not cache.primed


def test_age_grows_between_scrapes() -> None:
    clock = FakeTime()
    cache = StateCache(time_fn=clock)

    cache.update(_state(kv=0.5))
    assert state_age(cache.current()) == 0.0

    clock.t = 2.5
    assert state_age(cache.current()) == 2.5

    cache.update(_state(kv=0.6))
    assert state_age(cache.current()) == 0.0


def test_current_returns_scraped_signals_with_present_time() -> None:
    clock = FakeTime()
    cache = StateCache(time_fn=clock)
    cache.update(_state(kv=0.42))
    clock.t = 1.0

    state = cache.current()
    assert state.kv_used_fraction == 0.42  # as of the scrape
    assert state.now == 1.0  # but now is now
    assert state.engine_metrics["vllm:num_preemptions_total"] == 1.0


def test_a_failed_scrape_keeps_the_last_good_snapshot() -> None:
    """A blip should age the signal, not blind the policy outright."""
    clock = FakeTime()
    cache = StateCache(time_fn=clock)
    cache.update(_state(kv=0.5))

    clock.t = 1.0
    cache.record_failure()

    assert cache.current().kv_used_fraction == 0.5
    assert state_age(cache.current()) == 1.0
    assert cache.scrape_failures == 1


def test_stamping_does_not_mutate_the_scraped_object() -> None:
    cache = StateCache()
    original = _state(kv=0.3)

    cache.update(original)
    cache.current()

    assert STATE_AGE_KEY not in original.engine_metrics


def test_unstamped_state_reads_as_infinitely_old() -> None:
    assert state_age(_state(kv=0.1)) == math.inf


def test_scrape_count_tracks_updates() -> None:
    cache = StateCache()
    assert cache.scrapes == 0
    cache.update(_state(kv=0.1))
    cache.update(_state(kv=0.2))
    assert cache.scrapes == 2
    assert cache.primed
