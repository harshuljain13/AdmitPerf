"""The cached snapshot a policy reads when deciding.

A background task scrapes the engine every scrape_interval; the request path
reads whatever that task last wrote. It never scrapes inline, because putting
an HTTP round trip on every arrival would add latency to the decision and load
the engine with exactly the traffic admission control exists to shed.

The cost of that design is staleness, so every snapshot says how old it is.
"""

from __future__ import annotations

import dataclasses
import math
import time
from collections.abc import Callable

from admitperf.core.api import SystemState
from admitperf.core.ports import STATE_AGE_KEY


def empty_state(now: float) -> SystemState:
    """A snapshot with no signal in it.

    Optional fields are None, not zero. Zero is a claim — "the KV cache is
    empty" — and a policy acting on it would admit everything at the exact
    moment we know least about the fleet.
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
    """Holds the most recent scrape and stamps it with an age on read."""

    def __init__(self, *, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time = time_fn
        self._latest: SystemState | None = None
        self._captured_at: float | None = None
        self.scrapes = 0
        self.scrape_failures = 0

    def update(self, state: SystemState) -> None:
        """Record a successful scrape."""
        self._latest = state
        self._captured_at = self._time()
        self.scrapes += 1

    def record_failure(self) -> None:
        """Note a failed scrape.

        The last good snapshot is deliberately kept rather than cleared. It
        simply keeps ageing, and the staleness ceiling decides when it stops
        being usable — a brief blip should not blind the policy.
        """
        self.scrape_failures += 1

    def current(self) -> SystemState:
        """Latest snapshot, aged. Does no I/O."""
        now = self._time()
        if self._latest is None or self._captured_at is None:
            base, age = empty_state(now), math.inf
        else:
            base, age = self._latest, max(0.0, now - self._captured_at)

        metrics = dict(base.engine_metrics)
        metrics[STATE_AGE_KEY] = age
        return dataclasses.replace(base, now=now, engine_metrics=metrics)

    @property
    def primed(self) -> bool:
        return self._latest is not None


def state_age(state: SystemState) -> float:
    """Read the staleness stamp back out.

    An unstamped snapshot reads as infinitely old rather than fresh, which is
    the safe direction to be wrong in.
    """
    return float(state.engine_metrics.get(STATE_AGE_KEY, math.inf))


__all__ = ["StateCache", "empty_state", "state_age"]
