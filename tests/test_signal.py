"""Signal — the bridge between your metrics and a policy's input.

The conversion is the standard. If every host computed `kv_pressure` itself, two of
them reporting 0.93 would mean different things, which is exactly what the survey
found across sixteen papers. So these tests are about the conversion being
predictable, and about it refusing to invent a number when it cannot.
"""

from __future__ import annotations

import pytest

from admitperf.core.signal import Signal
from admitperf.core.signals import ALL, GPU_UTIL, KV_PRESSURE, PREFIX_HIT_RATE, QUEUE_DEPTH


def test_a_named_source_is_read_straight_through() -> None:
    assert KV_PRESSURE.read({"vllm:kv_cache_usage_perc": 0.93}) == pytest.approx(0.93)


def test_sources_are_tried_in_order() -> None:
    """A value the engine reports directly comes before anything derived, because a
    derivation only adds assumptions."""
    s = Signal("x", ["first", "second"])
    assert s.read({"second": 2.0}) == 2.0
    assert s.read({"first": 1.0, "second": 2.0}) == 1.0


def test_the_vllm_gauge_rename_is_a_non_event() -> None:
    """vLLM renamed gpu_cache_usage_perc -> kv_cache_usage_perc. Listing both means a
    version difference is not a portability failure, and the record still says which
    one was found so two reports stay distinguishable."""
    assert KV_PRESSURE.read({"vllm:gpu_cache_usage_perc": 0.5}) == 0.5
    assert KV_PRESSURE.read({"vllm:kv_cache_usage_perc": 0.5}) == 0.5
    assert KV_PRESSURE.source_of({"vllm:gpu_cache_usage_perc": 0.5}) == "vllm:gpu_cache_usage_perc"


def test_a_unit_conversion_ships_with_the_signal() -> None:
    """DCGM reports 0-100. A host should not have to know 94.0 means 0.94 here, and
    every host getting it wrong differently is how a shared name stops meaning
    anything."""
    assert GPU_UTIL.read({"DCGM_FI_DEV_GPU_UTIL": 94.0}) == pytest.approx(0.94)


def test_a_derived_source_combines_two_metrics() -> None:
    raw = {"vllm:prefix_cache_hits_total": 300.0, "vllm:prefix_cache_queries_total": 1000.0}
    assert PREFIX_HIT_RATE.read(raw) == pytest.approx(0.3)


def test_a_cold_cache_is_absent_not_a_crash() -> None:
    """Zero queries means no rate exists yet. That is absence, not an error for the
    host to handle, and not a hit rate of zero."""
    raw = {"vllm:prefix_cache_hits_total": 0.0, "vllm:prefix_cache_queries_total": 0.0}
    assert PREFIX_HIT_RATE.read(raw) is None


def test_absence_is_never_zero() -> None:
    """The most dangerous wrong answer available: a KV pressure of 0.0 claims the
    cache is empty, which reads as headroom — so the policy admits everything while
    appearing to work, and scores exactly like no policy at all."""
    assert KV_PRESSURE.read({}) is None
    assert KV_PRESSURE.read({"unrelated": 1.0}) is None


def test_a_value_outside_its_range_is_skipped_not_clamped() -> None:
    """Map a 0-100 metric to a fraction and clamping would give 1.0 — a cluster that
    looks permanently saturated. Skipping says "this source is wrong" instead."""
    s = Signal("frac", ["pct"], lo=0.0, hi=1.0)
    assert s.read({"pct": 94.0}) is None


def test_a_bad_source_falls_through_to_a_good_one() -> None:
    """So one mis-wired metric does not blind a signal that has another way in."""
    s = Signal("frac", ["pct", "frac"], lo=0.0, hi=1.0)
    assert s.read({"pct": 94.0, "frac": 0.94}) == pytest.approx(0.94)


def test_your_source_is_tried_first() -> None:
    """How a host whose metric is named differently participates: no subclass, no
    plugin, no registration call."""
    s = Signal("kv", ["vendor:kv"], lo=0.0, hi=1.0)
    s.add_source("acme:kv")
    assert s.read({"vendor:kv": 0.1, "acme:kv": 0.9}) == pytest.approx(0.9)


def test_a_callable_source_works_too() -> None:
    s = Signal("kv", lo=0.0, hi=1.0)
    s.add_source(lambda m: m["used"] / m["total"])
    assert s.read({"used": 380.0, "total": 400.0}) == pytest.approx(0.95)


def test_source_of_reports_which_one_won() -> None:
    """Recorded per decision, so two reports that resolved the same signal
    differently are distinguishable rather than silently incomparable."""
    assert QUEUE_DEPTH.source_of({"vllm:num_requests_waiting": 3}) == "vllm:num_requests_waiting"
    assert QUEUE_DEPTH.source_of({}) is None


def test_every_shipped_signal_has_help_and_a_floor() -> None:
    """`help` is what the report and the CLI print; a signal nobody can explain is a
    signal nobody should decide on. A floor of zero catches a counter read as a
    gauge going negative."""
    for s in ALL:
        assert s.help, s.name
        assert s.lo == 0.0, s.name


def test_fractions_declare_both_bounds() -> None:
    """Without an upper bound a fraction cannot catch percent, which is the likeliest
    binding mistake and is wrong by 100x."""
    for s in ALL:
        if s.hi is not None:
            assert (s.lo, s.hi) == (0.0, 1.0), s.name
