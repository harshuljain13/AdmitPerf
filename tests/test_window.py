"""Window — counters read as the change over a recent window, not since start."""

from __future__ import annotations

import pytest

from admitperf.core.signals import PREFIX_COUNTERS, PREFIX_HIT_RATE
from admitperf.core.window import Window

HITS, QUERIES = PREFIX_COUNTERS


def _scrape(hits: float, queries: float) -> dict[str, float]:
    return {HITS: hits, QUERIES: queries}


def test_a_collapse_in_reuse_shows_up_in_the_window() -> None:
    """After an hour at 90% reuse, a minute at 0% barely moves the cumulative rate.
    The windowed rate shows the collapse."""
    w = Window(PREFIX_COUNTERS, seconds=60)
    w.update(_scrape(90_000, 100_000), at=0)
    now = w.update(_scrape(90_000, 101_000), at=60)
    assert PREFIX_HIT_RATE.read(now) == 0.0
    assert PREFIX_HIT_RATE.read(_scrape(90_000, 101_000)) == pytest.approx(0.891, abs=1e-3)


def test_the_windowed_source_is_preferred_and_recorded() -> None:
    w = Window(PREFIX_COUNTERS)
    w.update(_scrape(0, 0), at=0)
    m = w.update(_scrape(30, 100), at=10)
    assert PREFIX_HIT_RATE.read(m) == pytest.approx(0.3)
    assert PREFIX_HIT_RATE.source_of(m) == "_vllm_prefix_hit_rate_window"


def test_the_first_scrape_falls_back_to_cumulative() -> None:
    """Nothing to subtract yet. The source says which value was used."""
    m = Window(PREFIX_COUNTERS).update(_scrape(30, 100), at=0)
    assert PREFIX_HIT_RATE.read(m) == pytest.approx(0.3)
    assert PREFIX_HIT_RATE.source_of(m) == "_vllm_prefix_hit_rate"


def test_samples_older_than_the_window_are_dropped() -> None:
    w = Window(PREFIX_COUNTERS, seconds=60)
    w.update(_scrape(0, 0), at=0)
    w.update(_scrape(100, 100), at=50)
    m = w.update(_scrape(100, 200), at=100)
    assert m[HITS + ":window"] == 0.0
    assert m[QUERIES + ":window"] == 100.0


def test_a_restart_resets_the_window_instead_of_going_negative() -> None:
    w = Window(PREFIX_COUNTERS)
    w.update(_scrape(500, 1000), at=0)
    m = w.update(_scrape(5, 10), at=10)
    assert HITS + ":window" not in m
    m = w.update(_scrape(15, 30), at=20)
    assert PREFIX_HIT_RATE.read(m) == pytest.approx(0.5)


def test_no_traffic_in_the_window_is_absent_not_zero() -> None:
    w = Window(PREFIX_COUNTERS)
    w.update(_scrape(50, 100), at=0)
    m = w.update(_scrape(50, 100), at=10)
    assert PREFIX_HIT_RATE.source_of(m) == "_vllm_prefix_hit_rate"


def test_the_window_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Window(PREFIX_COUNTERS, seconds=0)
