"""Task 2.2 — the state cache.

Two guarantees worth testing: reads are free (no I/O on the hot path), and an
un-ticked cache reports no signal rather than a confident zero.
"""

from __future__ import annotations

import math

from admitperf.core.api import SystemState
from admitperf.core.clock import VirtualClock
from admitperf.core.ports import STATE_AGE_KEY, StateSource
from admitperf.core.state import StateCache, state_age


def _state(kv: float, now: float = 0.0) -> SystemState:
    return SystemState(
        now=now,
        kv_used_fraction=kv,
        running_requests=3,
        waiting_requests=2,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={"vllm:num_preemptions_total": 1.0},
    )


def test_cache_satisfies_the_state_source_port() -> None:
    assert isinstance(StateCache(VirtualClock(), engine_name="replay"), StateSource)


def test_before_any_tick_there_is_no_signal_not_a_zero() -> None:
    """An un-ticked cache must not claim the KV cache is empty.

    Zero is a claim. A policy reading 0.0 would conclude there is plenty of
    room and admit everything, at the moment we know least about the fleet.
    """
    cache = StateCache(VirtualClock(), engine_name="replay")
    state = cache.current()

    assert state.kv_used_fraction is None
    assert state_age(state) == math.inf


def test_age_grows_with_the_clock() -> None:
    clock = VirtualClock()
    cache = StateCache(clock, engine_name="replay")

    cache.update(_state(kv=0.5))
    assert state_age(cache.current()) == 0.0

    clock.advance(2.5)
    assert state_age(cache.current()) == 2.5

    # A fresh tick resets it.
    cache.update(_state(kv=0.6))
    assert state_age(cache.current()) == 0.0


def test_current_returns_latest_signals_with_present_time() -> None:
    clock = VirtualClock()
    cache = StateCache(clock, engine_name="replay")
    cache.update(_state(kv=0.42))
    clock.advance(1.0)

    state = cache.current()
    assert state.kv_used_fraction == 0.42  # signals are as of the tick
    assert state.now == 1.0  # but `now` is the present
    assert state.engine_metrics["vllm:num_preemptions_total"] == 1.0


def test_update_does_not_mutate_the_caller_snapshot() -> None:
    """The cache stamps age on a copy; the engine's object stays clean."""
    clock = VirtualClock()
    cache = StateCache(clock, engine_name="replay")
    original = _state(kv=0.3)

    cache.update(original)
    cache.current()

    assert STATE_AGE_KEY not in original.engine_metrics


def test_unstamped_state_is_treated_as_infinitely_old() -> None:
    """Safe direction to be wrong in: unknown age must not read as fresh."""
    assert state_age(_state(kv=0.1)) == math.inf


def test_tick_count_tracks_updates() -> None:
    cache = StateCache(VirtualClock(), engine_name="replay")
    assert cache.tick_count == 0
    cache.update(_state(kv=0.1))
    cache.update(_state(kv=0.2))
    assert cache.tick_count == 2
