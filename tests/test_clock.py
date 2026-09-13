"""Task 1.3 — clocks (spec D1).

The guarantee under test: VirtualClock reaches a distant instant without
spending wall-clock time. That is what makes a 1000-request replay finish in
milliseconds and produce identical results every run.
"""

from __future__ import annotations

import time

import pytest

from admitperf.core.clock import VirtualClock, WallClock


async def test_virtual_clock_does_not_actually_sleep() -> None:
    clock = VirtualClock()
    started = time.monotonic()

    await clock.sleep_until(3600.0)  # one hour of simulated time

    assert clock.now() == 3600.0
    assert time.monotonic() - started < 0.5, "VirtualClock slept for real"


async def test_virtual_clock_is_monotonic() -> None:
    """A sleep_until in the past must not rewind the timeline."""
    clock = VirtualClock()
    await clock.sleep_until(10.0)
    await clock.sleep_until(5.0)
    assert clock.now() == 10.0


def test_virtual_clock_rejects_negative_advance() -> None:
    clock = VirtualClock()
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(-1.0)


async def test_wall_clock_advances_and_sleeps() -> None:
    clock = WallClock()
    assert clock.now() >= 0.0
    started = clock.now()
    await clock.sleep_until(started + 0.02)
    assert clock.now() >= started + 0.02


async def test_wall_clock_past_deadline_returns_immediately() -> None:
    clock = WallClock()
    started = time.monotonic()
    await clock.sleep_until(clock.now() - 5.0)
    assert time.monotonic() - started < 0.5


def test_both_clocks_satisfy_the_protocol() -> None:
    from admitperf.core.ports import Clock

    assert isinstance(VirtualClock(), Clock)
    assert isinstance(WallClock(), Clock)
