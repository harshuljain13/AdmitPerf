"""The state cache — what a policy sees when it is asked to decide.

docs/design.md is explicit that state is *pushed* on the engine's own tick and
read from cache on each arrival, never scraped per request. The reason is
concrete: at a few thousand requests per second, scraping /metrics per arrival
would put the decision path behind an HTTP round trip and hammer the engine
with exactly the load it is trying to shed.

So `current()` does no I/O. It hands back the most recent snapshot plus how
stale it is, and lets the caller decide whether that is good enough (spec D9).
"""

from __future__ import annotations

import dataclasses
import math

from admitperf.core.api import SystemState
from admitperf.core.ports import STATE_AGE_KEY, Clock


def empty_state(now: float) -> SystemState:
    """A snapshot carrying no signal at all.

    Returned before the first tick arrives. Every optional field is None rather
    than zero, because zero is a *claim* — "the cache is empty" — and acting on
    it would admit everything at exactly the moment we know least.
    """
    return SystemState(
        now=now,
        kv_used_fraction=None,
        running_requests=0,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )


class StateCache:
    """Holds the latest engine tick and stamps it with an age on read."""

    def __init__(
        self,
        clock: Clock,
        *,
        engine_name: str,
        capabilities: frozenset[str] = frozenset(),
    ) -> None:
        self._clock = clock
        self._engine_name = engine_name
        self._capabilities = capabilities
        self._latest: SystemState | None = None
        self._captured_at: float | None = None
        self._tick_count = 0

    def update(self, state: SystemState) -> None:
        """Record a tick. Called by the engine, never by the runner."""
        self._latest = state
        self._captured_at = self._clock.now()
        self._tick_count += 1

    def current(self) -> SystemState:
        """Latest snapshot, aged. Performs no I/O.

        `now` is the present instant, while the signals are as of the last
        tick; the gap between them is `admitperf:state_age_s`. A policy that
        cares can read it, and the runner enforces a ceiling on it.
        """
        now = self._clock.now()
        if self._latest is None or self._captured_at is None:
            base = empty_state(now)
            age = math.inf
        else:
            base = self._latest
            age = max(0.0, now - self._captured_at)

        metrics = dict(base.engine_metrics)
        metrics[STATE_AGE_KEY] = age
        return dataclasses.replace(base, now=now, engine_metrics=metrics)

    @property
    def engine_name(self) -> str:
        """Recorded in the run manifest, not in engine_metrics — that dict is
        typed float-only, and the engine identity is a property of the run."""
        return self._engine_name

    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def tick_count(self) -> int:
        return self._tick_count


def state_age(state: SystemState) -> float:
    """Read the staleness stamp back out of a snapshot.

    Age lives in the free-form metrics dict because frozen API v0 has no field
    for it (spec D2). A snapshot that was never stamped is treated as
    infinitely old rather than fresh — the safe direction to be wrong in.
    """
    value = state.engine_metrics.get(STATE_AGE_KEY, math.inf)
    return float(value)


__all__ = ["StateCache", "empty_state", "state_age"]
