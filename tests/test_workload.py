"""Task 2.5 — the synthetic workload.

The guarantee that matters: same seed, same stream. Without it, two policy runs
differ by both the policy and the traffic, and nothing can be attributed.
"""

from __future__ import annotations

import random
import statistics

import pytest

from admitperf.bench.workloads.poisson import DEFAULT_CLASSES, PoissonWorkload


def test_same_seed_gives_an_identical_stream() -> None:
    a = list(PoissonWorkload(n_requests=200, seed=42).requests())
    b = list(PoissonWorkload(n_requests=200, seed=42).requests())
    assert a == b


def test_different_seed_gives_a_different_stream() -> None:
    a = list(PoissonWorkload(n_requests=200, seed=1).requests())
    b = list(PoissonWorkload(n_requests=200, seed=2).requests())
    assert a != b


def test_generation_is_immune_to_global_random_state() -> None:
    """A policy or engine calling random.random() must not shift the workload.

    If it could, two runs would differ by traffic as well as by policy, and the
    comparison would be meaningless.
    """
    random.seed(1)
    a = list(PoissonWorkload(n_requests=50, seed=7).requests())
    random.seed(999)
    [random.random() for _ in range(100)]
    b = list(PoissonWorkload(n_requests=50, seed=7).requests())
    assert a == b


def test_arrivals_are_ordered_and_start_after_zero() -> None:
    times = [r.arrival_time for r in PoissonWorkload(n_requests=500, seed=3).requests()]
    assert times == sorted(times)
    assert times[0] > 0.0


def test_mean_gap_approximates_the_requested_rate() -> None:
    """Poisson: mean inter-arrival gap should be about 1/rate."""
    rate = 20.0
    times = [
        r.arrival_time
        for r in PoissonWorkload(n_requests=4000, rate_per_s=rate, seed=11).requests()
    ]
    gaps = [b - a for a, b in zip(times[:-1], times[1:], strict=True)]
    assert statistics.mean(gaps) == pytest.approx(1.0 / rate, rel=0.1)


def test_arrivals_are_bursty_not_evenly_spaced() -> None:
    """Bursts are the point: a policy that only sees smooth traffic is never
    actually tested. For an exponential distribution, stdev ~= mean."""
    times = [
        r.arrival_time
        for r in PoissonWorkload(n_requests=4000, rate_per_s=20.0, seed=12).requests()
    ]
    gaps = [b - a for a, b in zip(times[:-1], times[1:], strict=True)]
    assert statistics.stdev(gaps) == pytest.approx(statistics.mean(gaps), rel=0.2)


def test_every_slo_class_appears_with_its_deadlines() -> None:
    reqs = list(PoissonWorkload(n_requests=2000, seed=5).requests())
    seen = {r.slo_class for r in reqs}
    assert seen == {c.name for c in DEFAULT_CLASSES}

    interactive = next(r for r in reqs if r.slo_class == "interactive")
    assert interactive.deadline_ttft_ms == 500.0
    assert interactive.priority == 10


def test_request_ids_are_unique() -> None:
    reqs = list(PoissonWorkload(n_requests=1000, seed=8).requests())
    assert len({r.request_id for r in reqs}) == 1000


def test_events_view_matches_requests_view() -> None:
    w = PoissonWorkload(n_requests=20, seed=4)
    reqs = list(w.requests())
    events = list(w.events())

    assert w.total_events() == 20
    assert [e.request_id for e in events] == [r.request_id for r in reqs]
    # Single-turn traffic: each request is its own session of one.
    assert all(e.agent_id == e.request_id and e.is_final_turn for e in events)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"n_requests": 0},
        {"rate_per_s": 0.0},
        {"classes": ()},
        {"tenants": ()},
    ],
)
def test_rejects_nonsense_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PoissonWorkload(**kwargs)  # type: ignore[arg-type]


# --- duration, warmup, relative deadlines ---------------------------------


def test_duration_mode_stops_on_time_not_on_count() -> None:
    """Preferred for comparisons: with a fixed count a heavy-shedding policy
    finishes early and is measured over a different window."""
    from admitperf.bench.workloads.poisson import PoissonWorkload as W

    reqs = list(W(duration_s=5.0, rate_per_s=20.0, seed=0).requests())
    assert reqs[-1].arrival_time <= 5.0
    assert len(reqs) > 50  # not the default n


def test_warmup_requests_are_marked_not_dropped() -> None:
    """They are still sent — they are part of the load the engine faces — but
    flagged so the summary can exclude them."""
    from admitperf.bench.workloads.poisson import PoissonWorkload as W

    reqs = list(W(duration_s=5.0, warmup_s=2.0, rate_per_s=20.0, seed=0).requests())
    warm = [r for r in reqs if r.metadata["warmup"]]

    assert warm, "warmup window produced no requests"
    assert all(r.arrival_time < 2.0 for r in warm)
    assert all(not r.metadata["warmup"] for r in reqs if r.arrival_time >= 2.0)


def test_relative_deadlines_scale_off_the_measured_baseline() -> None:
    """500ms is generous for a small model and impossible for a large one, so a
    fixed threshold measures the model rather than the policy."""
    from admitperf.bench.workloads.poisson import Baseline
    from admitperf.bench.workloads.poisson import PoissonWorkload as W

    slow = list(W(n_requests=200, seed=1, baseline=Baseline(400.0, 40.0)).requests())
    fast = list(W(n_requests=200, seed=1, baseline=Baseline(100.0, 10.0)).requests())

    slow_i = next(r for r in slow if r.slo_class == "interactive")
    fast_i = next(r for r in fast if r.slo_class == "interactive")

    # Same 3x multiplier, four times the allowance on the slower engine.
    assert slow_i.deadline_ttft_ms == pytest.approx(fast_i.deadline_ttft_ms * 4)


def test_absolute_deadlines_are_used_when_no_baseline_is_given() -> None:
    from admitperf.bench.workloads.poisson import PoissonWorkload as W

    req = next(r for r in W(n_requests=200, seed=1).requests() if r.slo_class == "interactive")
    assert req.deadline_ttft_ms == 500.0
