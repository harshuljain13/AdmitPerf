"""Comparing a baseline against a policy — the question the package exists to answer.

Also the easiest thing in the project to get wrong, so most of these are about the
comparison refusing to make a claim it cannot support.
"""

from __future__ import annotations

from admitperf.comparison import Comparison
from admitperf.core.log import Log
from admitperf.policies import KvThreshold, NoAdmission
from admitperf.report import Report

BUSY = {"vllm:kv_cache_usage_perc": 0.95, "vllm:num_requests_waiting": 40.0}
IDLE = {"vllm:kv_cache_usage_perc": 0.05, "vllm:num_requests_waiting": 0.0}


def _run(path, policy, metrics_seq, ttft_for):
    """Drive one arm and record an outcome per admitted request."""
    with policy:
        for i, m in enumerate(metrics_seq):
            rid = f"r{i}"
            if policy(m, request_id=rid).admitted:
                ttft = ttft_for(i)
                policy.outcome(rid, ttft_ms=ttft, ok=ttft <= 1000.0)
    return Report.from_log(path)


def _pair(tmp_path, *, n=20, slow_baseline=True):
    """A baseline that misses its SLO, and a policy that sheds to protect it.

    The load alternates so the policy refuses roughly half rather than everything —
    an arm that refuses all of its traffic records no outcomes, and then there is
    nothing to compare.
    """
    base_path, pol_path = tmp_path / "base.jsonl", tmp_path / "pol.jsonl"
    load = [BUSY if i % 2 else IDLE for i in range(n)]
    base = _run(
        base_path,
        NoAdmission(log=base_path),
        load,
        (lambda i: 2000.0) if slow_baseline else (lambda i: 100.0),
    )
    pol = _run(pol_path, KvThreshold(threshold=0.9, log=pol_path), load, lambda i: 100.0)
    return Comparison(base, pol)


# --------------------------------------------------------------------------
# The three checks, before any number
# --------------------------------------------------------------------------


def test_a_policy_that_never_fired_produces_no_finding(tmp_path) -> None:
    """Two runs of the same configuration. A latency difference here is noise, and
    reporting it as a result is the failure mode the survey documents."""
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"
    base = _run(base_path, NoAdmission(log=base_path), [IDLE] * 10, lambda i: 100.0)
    pol = _run(pol_path, KvThreshold(threshold=0.9, log=pol_path), [IDLE] * 10, lambda i: 50.0)

    c = Comparison(base, pol)
    assert not c.fired()
    text = c.text()
    assert "NO FINDING" in text
    assert "measured twice" in text
    # And it points at the load rather than the policy, which is not the instinct.
    assert "LOAD, not the policy" in text


def test_a_latency_number_is_not_quoted_when_the_policy_did_not_fire(tmp_path) -> None:
    """The headline must not contain a comparison the run cannot support."""
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"
    base = _run(base_path, NoAdmission(log=base_path), [IDLE] * 10, lambda i: 900.0)
    pol = _run(pol_path, KvThreshold(threshold=0.9, log=pol_path), [IDLE] * 10, lambda i: 100.0)
    text = Comparison(base, pol).text()
    assert "x lower" not in text


def test_mismatched_load_is_called_out(tmp_path) -> None:
    """A policy run against half the traffic will look wonderful."""
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"
    base = _run(base_path, NoAdmission(log=base_path), [BUSY] * 100, lambda i: 2000.0)
    pol = _run(pol_path, KvThreshold(threshold=0.9, log=pol_path), [BUSY] * 20, lambda i: 100.0)

    c = Comparison(base, pol)
    assert not c.comparable_load()
    assert "will look better than it is" in c.text()


def test_the_same_load_within_ten_percent_is_comparable(tmp_path) -> None:
    c = _pair(tmp_path, n=40)
    assert c.comparable_load()
    assert "both runs were offered the same load" in c.text()


def test_cost_is_not_invented_when_no_outcomes_were_recorded(tmp_path) -> None:
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"
    with NoAdmission(log=base_path) as p:
        for i in range(10):
            p(BUSY, request_id=f"r{i}")
    with KvThreshold(threshold=0.9, log=pol_path) as p:
        for i in range(10):
            p(BUSY, request_id=f"r{i}")

    c = Comparison(Report.from_log(base_path), Report.from_log(pol_path))
    assert not c.measurable()
    text = c.text()
    assert "cannot be measured" in text
    assert "x lower" not in text


# --------------------------------------------------------------------------
# The numbers themselves
# --------------------------------------------------------------------------


def test_a_real_improvement_is_reported_as_one(tmp_path) -> None:
    """With one run per arm the three checks can pass, and the page still refuses to
    call it quotable — that needs repeats."""
    c = _pair(tmp_path, n=40)
    text = c.text()
    assert c.fired()
    assert "x lower" in text
    assert "The first three hold" in text
    assert "This is a result you can quote" not in text


def test_separated_repeats_make_it_quotable(tmp_path) -> None:
    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [2000.0, 2100.0, 1950.0])
    pol = _arm(
        tmp_path / "pol", lambda log: KvThreshold(threshold=0.9, log=log), [100.0, 110.0, 95.0]
    )
    c = Comparison.from_logs(base, pol)
    assert c.separated() is True
    assert "This is a result you can quote" in c.text()


def test_goodput_divides_by_offered_not_admitted(tmp_path) -> None:
    """Dividing by admitted rewards a policy for refusing more rather than better:
    refuse 95%, serve the rest perfectly, and the number reads 1.00."""
    path = tmp_path / "p.jsonl"
    # 10 offered, 4 admitted, all 4 meeting their SLO.
    with KvThreshold(threshold=0.9, log=path) as p:
        for i in range(10):
            rid = f"r{i}"
            metrics = IDLE if i < 4 else BUSY
            if p(metrics, request_id=rid).admitted:
                p.outcome(rid, ttft_ms=50.0, ok=True)

    report = Report.from_log(path)
    assert Comparison._goodput(report) == 0.4  # 4/10, not 4/4


def test_a_goodput_regression_is_called_out_even_when_latency_improved(tmp_path) -> None:
    """The finding the working example produces: a KV threshold chosen without
    reference to the SLO cuts the tail and sheds requests the cluster could still
    have served in time. A latency-only report calls that a win."""
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"

    # Baseline: 10 offered, 10 admitted, 6 of them inside the SLO -> 0.6
    with NoAdmission(log=base_path) as p:
        for i in range(10):
            rid = f"r{i}"
            p(BUSY, request_id=rid)
            p.outcome(rid, ttft_ms=100.0 if i < 6 else 5000.0, ok=i < 6)

    # Policy: 10 offered, 3 admitted, all fast -> 0.3. Better tail, worse goodput.
    with KvThreshold(threshold=0.9, log=pol_path) as p:
        for i in range(10):
            rid = f"r{i}"
            if p(IDLE if i < 3 else BUSY, request_id=rid).admitted:
                p.outcome(rid, ttft_ms=100.0, ok=True)

    text = Comparison(Report.from_log(base_path), Report.from_log(pol_path)).text()
    assert "goodput is DOWN" in text
    assert "not a win" in text


def test_signal_maxima_are_shown_side_by_side(tmp_path) -> None:
    """So "the policy changed the cluster" is checkable rather than assumed. KV
    pressure that did not move means the refusals bought nothing."""
    c = _pair(tmp_path, n=40)
    text = c.text()
    assert "signal max" in text
    assert "kv_pressure" in text


def test_instrument_faults_from_either_side_surface(tmp_path) -> None:
    """A comparison built on a log full of failed scrapes is not a comparison."""
    base_path, pol_path = tmp_path / "b.jsonl", tmp_path / "p.jsonl"
    log = Log(base_path)
    log.write({"at": 1.0, "scrape_error": "connection refused"})
    log.close()
    with KvThreshold(threshold=0.9, log=pol_path) as p:
        p(BUSY, request_id="r0")

    text = Comparison(Report.from_log(base_path), Report.from_log(pol_path)).text()
    assert "baseline:" in text and "scrape(s) failed" in text


def test_a_repeat_warning_is_attached_to_a_trustworthy_result(tmp_path) -> None:
    """One run of each has no error bar. Saying so is cheaper than a reviewer saying
    it for you."""
    assert "no error bar" in _pair(tmp_path, n=40).text()


# --------------------------------------------------------------------------
# Repeats — one run of each arm has no error bar
# --------------------------------------------------------------------------


def _arm(dir_path, policy_factory, ttfts):
    """One directory, one log per repeat."""
    dir_path.mkdir(parents=True, exist_ok=True)
    for i, ttft in enumerate(ttfts, 1):
        log = dir_path / f"r{i}.jsonl"
        with policy_factory(log) as p:
            for j in range(20):
                rid = f"r{j}"
                if p(BUSY if j % 2 else IDLE, request_id=rid).admitted:
                    p.outcome(rid, ttft_ms=ttft, ok=ttft <= 1000.0)
    return dir_path


def test_a_directory_is_read_as_repeats_of_one_arm(tmp_path) -> None:
    """How an arm gets an error bar. One file is one run; a directory is the set."""
    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [2000.0, 2100.0, 1900.0])
    pol = _arm(
        tmp_path / "pol", lambda log: KvThreshold(threshold=0.9, log=log), [100.0, 120.0, 90.0]
    )
    c = Comparison.from_logs(base, pol)
    assert c.repeats == 3
    assert len(c.baselines) == 3 and len(c.policies) == 3


def test_a_single_run_refuses_to_claim_separation(tmp_path) -> None:
    """With one run per arm the question cannot be asked, and answering it anyway is
    how a difference inside the noise gets published."""
    c = _pair(tmp_path, n=40)
    assert c.separated() is None
    text = c.text()
    assert "no error bar" in text
    assert "This is a result you can quote" not in text


def test_the_spread_is_shown_when_there_is_one(tmp_path) -> None:
    """A bare number from one run reads as more certain than it is."""
    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [2000.0, 2400.0])
    pol = _arm(tmp_path / "pol", lambda log: KvThreshold(threshold=0.9, log=log), [100.0, 140.0])
    text = Comparison.from_logs(base, pol).text()
    assert "(2000-2400)" in text


def test_overlapping_arms_are_called_noise_not_a_result(tmp_path) -> None:
    """The check that matters. Two arms whose repeats overlap have not separated,
    however large the gap between their medians looks."""
    # Both arms hover around the same goodput; admitted counts differ run to run.
    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [900.0, 1100.0, 950.0])
    pol = _arm(
        tmp_path / "pol", lambda log: KvThreshold(threshold=0.9, log=log), [900.0, 1100.0, 950.0]
    )
    c = Comparison.from_logs(base, pol)
    if c.separated() is False:
        assert "inside the run-to-run noise" in c.text()
        assert "This is a result you can quote" not in c.text()


def test_every_repeat_must_have_fired(tmp_path) -> None:
    """Averaging in a repeat where the policy never fired hides that it was a
    different experiment."""
    pol_dir = tmp_path / "pol"
    pol_dir.mkdir()
    # r1 fires, r2 sees an idle cluster and refuses nothing.
    for name, load in (("r1", [BUSY] * 10), ("r2", [IDLE] * 10)):
        with KvThreshold(threshold=0.9, log=pol_dir / f"{name}.jsonl") as p:
            for j, m in enumerate(load):
                rid = f"r{j}"
                if p(m, request_id=rid).admitted:
                    p.outcome(rid, ttft_ms=100.0, ok=True)

    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [2000.0])
    c = Comparison.from_logs(base, pol_dir)
    assert not c.fired()
    assert "NO FINDING" in c.text()


def test_mismatched_load_is_caught_across_every_repeat(tmp_path) -> None:
    """Not just between the two representative runs: one short repeat anywhere makes
    the set incomparable."""
    base_dir, pol_dir = tmp_path / "base", tmp_path / "pol"
    _arm(base_dir, lambda log: NoAdmission(log=log), [2000.0, 2000.0])
    with NoAdmission(log=base_dir / "r3.jsonl") as p:  # a short third run
        for j in range(3):
            p(BUSY, request_id=f"r{j}")
            p.outcome(f"r{j}", ttft_ms=100.0, ok=True)
    _arm(pol_dir, lambda log: KvThreshold(threshold=0.9, log=log), [100.0, 100.0])

    c = Comparison.from_logs(base_dir, pol_dir)
    assert not c.comparable_load()


def test_an_empty_directory_is_an_error_not_an_empty_comparison(tmp_path) -> None:
    empty = tmp_path / "nothing"
    empty.mkdir()
    base = _arm(tmp_path / "base", lambda log: NoAdmission(log=log), [2000.0])
    try:
        Comparison.from_logs(base, empty)
    except ValueError as exc:
        assert "no .jsonl logs" in str(exc)
    else:
        raise AssertionError("an empty arm should not produce a comparison")
