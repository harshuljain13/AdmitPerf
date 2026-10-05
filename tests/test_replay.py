"""Replaying a policy over a trace recorded without it.

This exists because a `watch` trace alone cannot report signal liveness — there is no
policy in it, so no threshold to judge the observed range against, and item 3 reads
*unevidenced* while the answer sits in the file. Replay supplies the threshold.
"""

from __future__ import annotations

import pytest

from admitperf.core.log import Log
from admitperf.policies import KvThreshold, NoAdmission, QueueDepth
from admitperf.replay import Replay
from admitperf.report import Report
from admitperf.status import Status


def _trace(path, *, kv_max: float, queue_max: float, n: int = 120, failures: int = 0):
    """A watch-shaped trace: metric snapshots, no decisions."""
    log = Log(path)
    for i in range(failures):
        log.write({"at": float(i), "scrape_error": "connection refused"})
    for i in range(n):
        kv = min(kv_max, kv_max * i / max(1, n // 2))
        q = min(queue_max, float(i))
        log.write(
            {
                "at": float(i),
                "metrics": {
                    "vllm:kv_cache_usage_perc": kv,
                    "vllm:num_requests_waiting": q,
                },
                "signals": {"kv_pressure": kv, "queue_depth": q},
            }
        )
    log.close()
    return path


def test_a_watch_trace_alone_cannot_evidence_liveness(tmp_path) -> None:
    """The gap replay closes. Worth pinning, because it is the reason replay exists:
    the data is present and has nothing to be compared against."""
    r = Report.from_log(_trace(tmp_path / "t.jsonl", kv_max=0.44, queue_max=61))
    assert r.verdict() == "UNKNOWN"
    assert r.threshold is None
    item3 = next(i for i in r.items() if i.number == 3)
    assert item3.status is Status.UNEVIDENCED


def test_replaying_a_policy_supplies_the_verdict(tmp_path) -> None:
    """0.44 against a 0.90 threshold: the policy could not have fired, and now the
    report says so instead of shrugging."""
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.44, queue_max=61)
    rep = Replay.from_log(trace)
    log = rep.against(KvThreshold(threshold=0.90), out=tmp_path / "out.jsonl", experiment="e")

    r = Report.from_log(log)
    assert r.verdict() == "INERT"
    assert r.threshold == 0.90
    item3 = next(i for i in r.items() if i.number == 3)
    assert item3.status is Status.FAIL
    assert "99" in item3.detail or "short" in item3.detail


def test_a_signal_that_did_cross_reads_live(tmp_path) -> None:
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.97, queue_max=10)
    log = Replay.from_log(trace).against(
        KvThreshold(threshold=0.90), out=tmp_path / "out.jsonl", experiment="e"
    )
    assert Report.from_log(log).verdict() == "LIVE"


def test_the_same_trace_judges_several_policies(tmp_path) -> None:
    """The strongest thing replay offers, and what no published comparison does: N
    policies, one real trace, identical inputs, zero run-to-run variance.

    Here KV never moves but the queue does — so one policy is inert and the other
    fires, from the same recording.
    """
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.44, queue_max=61)
    rep = Replay.from_log(trace)

    kv_log = rep.against(KvThreshold(threshold=0.90), out=tmp_path / "kv.jsonl", experiment="e")
    q_log = rep.against(QueueDepth(max_waiting=32), out=tmp_path / "q.jsonl", experiment="e")

    assert Report.from_log(kv_log).verdict() == "INERT"
    assert Report.from_log(q_log).verdict() == "LIVE"


def test_failed_scrapes_are_not_replayed_against(tmp_path) -> None:
    """A policy cannot have decided on a reading that was never taken, and inventing a
    decision there would pad the denominator with samples nobody observed."""
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.95, queue_max=5, n=50, failures=7)
    rep = Replay.from_log(trace)
    assert len(rep.samples) == 50
    assert rep.skipped == 7


def test_duty_cycle_is_not_a_refusal_rate(tmp_path) -> None:
    """Named carefully. Samples are periodic and arrivals are not, so the share of
    samples in a refusing state differs from the share of requests refused by however
    bursty the traffic was. Converting one to the other needs arrival timestamps, which
    a watch trace does not carry.
    """
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.95, queue_max=5)
    rep = Replay.from_log(trace)
    duty = rep.duty_cycle(KvThreshold(threshold=0.90))
    assert 0.0 < duty < 1.0
    assert rep.duty_cycle(NoAdmission()) == 0.0


def test_a_trace_of_nothing_but_failures_is_refused(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    log = Log(path)
    for i in range(5):
        log.write({"at": float(i), "scrape_error": "refused"})
    log.close()

    rep = Replay.from_log(path)
    assert not rep.samples
    with pytest.raises(ValueError):
        rep.against(KvThreshold(threshold=0.9), out=tmp_path / "o.jsonl", experiment="e")


def test_the_replayed_log_is_shaped_like_a_live_one(tmp_path) -> None:
    """Every consumer downstream should be unable to tell the difference; the only
    thing that differs is where the metrics came from."""
    trace = _trace(tmp_path / "t.jsonl", kv_max=0.95, queue_max=5)
    log = Replay.from_log(trace).against(
        KvThreshold(threshold=0.90), out=tmp_path / "out.jsonl", experiment="kv-wall"
    )
    rec = Log.read(log)[0]
    for field in ("experiment", "run", "policy", "params", "verdict", "signals", "metrics"):
        assert field in rec, field
    assert rec["experiment"] == "kv-wall"
