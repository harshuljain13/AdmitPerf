"""Clocks — wall time for live runs, virtual time for replay (spec D1).

This is the decision the rest of the MVP rests on. `04-dynamic.mmd` claims one
sequence serves both benchmark replay and runtime middleware; that is only true
if time is injected. With ambient `time.monotonic()` calls inside the runner,
a replay would take as long as the trace it replays, and determinism would be
impossible.

`VirtualClock.sleep_until` advances a counter and returns immediately, so a
1000-request trace replays in milliseconds and produces identical results every
time. `WallClock.sleep_until` actually sleeps. Nothing downstream knows which
one it holds.
"""

from __future__ import annotations

import asyncio
import time


class WallClock:
    """Real time. Used for live runs against a real engine."""

    def __init__(self, *, epoch: float | None = None) -> None:
        self._epoch = time.monotonic() if epoch is None else epoch

    def now(self) -> float:
        return time.monotonic() - self._epoch

    async def sleep_until(self, t: float) -> None:
        delay = t - self.now()
        if delay > 0:
            await asyncio.sleep(delay)


class VirtualClock:
    """Simulated time. Never sleeps; jumps straight to the requested instant.

    Time is monotonic by construction: `sleep_until` in the past is a no-op
    rather than a rewind, so an out-of-order event cannot corrupt the timeline.
    """

    def __init__(self, *, start: float = 0.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    async def sleep_until(self, t: float) -> None:
        if t > self._now:
            self._now = t
        # Yield so other tasks make progress; costs no wall-clock time.
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        """Move time forward by a delta. Rejects negative deltas loudly."""
        if seconds < 0:
            raise ValueError(f"cannot advance time backwards: {seconds}")
        self._now += seconds


__all__ = ["VirtualClock", "WallClock"]
