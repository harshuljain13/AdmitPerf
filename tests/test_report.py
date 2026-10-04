"""Report — what a log can and cannot support."""

from __future__ import annotations

from admitperf.core.log import Log
from admitperf.core.policy import Policy
from admitperf.core.signals import KV_PRESSURE
from admitperf.report import Report


class KvWall(Policy):
    name = "kv_wall"

    def decide(self, metrics):
        if KV_PRESSURE.read(metrics) >= self.threshold:
            return self.reject("kv_pressure")
        return self.admit()


BUSY = {"vllm:kv_cache_usage_perc": 0.95, "vllm:num_requests_waiting": 41.0}
IDLE = {"vllm:kv_cache_usage_perc": 0.05, "vllm:num_requests_waiting": 0.0}


def _log(tmp_path, metrics_seq, *, threshold=0.9, enforce=1.0):
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=threshold, log=path, enforce=enforce) as p:
        for i, m in enumerate(metrics_seq):
            p(m, request_id=f"r{i}")
    return path


# --- the validity gate ----------------------------------------------------


def test_a_policy_that_refused_is_live(tmp_path) -> None:
    r = Report.from_log(_log(tmp_path, [BUSY] * 5))
    assert r.verdict() == "LIVE"
    assert r.refused == 5
    assert "refused 5 of 5" in r.text()


def test_a_policy_that_refused_nothing_is_inert(tmp_path) -> None:
    """The finding none of the sixteen surveyed papers reports. Without it a latency
    table from this log would be a measurement of two identical configurations."""
    r = Report.from_log(_log(tmp_path, [IDLE] * 5))
    assert r.verdict() == "INERT"
    assert "indistinguishable from no policy" in r.text()
    # And it points at the load rather than the policy, which is not the instinct.
    assert "LOAD, not the policy" in r.text()


def test_a_state_only_log_claims_nothing_about_a_policy(tmp_path) -> None:
    """What `watch` produces. It is useful — it tells you which signal moved — and it
    must not be dressed up as evidence about a policy that never ran."""
    path = tmp_path / "t.jsonl"
    log = Log(path)
    log.write({"at": 1.0, "metrics": BUSY, "signals": {"kv_pressure": 0.95}})
    log.close()

    r = Report.from_log(path)
    assert r.verdict() == "UNKNOWN"
    assert "No policy ran" in r.text()


# --- which signals moved --------------------------------------------------


def test_every_signal_gets_a_range_not_just_the_one_read(tmp_path) -> None:
    """The asymmetry the design rests on. This is how you learn your signal was the
    wrong one for the regime."""
    r = Report.from_log(_log(tmp_path, [BUSY, IDLE]))
    ranges = r.signals()
    assert ranges["kv_pressure"]["max"] == 0.95
    assert ranges["queue_depth"]["max"] == 41.0  # never read by the policy
    assert ranges["prefix_hit_rate"] is None


def test_percentiles_are_values_the_signal_actually_took(tmp_path) -> None:
    """An interpolated p95 can report a number the signal never reached, which
    defeats the purpose of showing a range."""
    seq = [{"vllm:kv_cache_usage_perc": v} for v in (0.1, 0.2, 0.3, 0.4)]
    rng = Report.from_log(_log(tmp_path, seq)).signal_range("kv_pressure")
    for key in ("min", "p50", "p95", "max"):
        assert rng[key] in (0.1, 0.2, 0.3, 0.4)


def test_the_threshold_is_stated_so_a_range_can_be_judged(tmp_path) -> None:
    r = Report.from_log(_log(tmp_path, [IDLE] * 3, threshold=0.9))
    assert r.threshold == 0.9
    assert "threshold was 0.9" in r.text()


def test_an_absent_signal_is_reported_as_absent(tmp_path) -> None:
    text = Report.from_log(_log(tmp_path, [IDLE])).text()
    assert "never supplied by your metrics" in text
    assert "absent, not zero" in text


# --- what it did and what it cost ----------------------------------------


def test_refusals_are_grouped_by_reason(tmp_path) -> None:
    r = Report.from_log(_log(tmp_path, [BUSY] * 3))
    assert r.by_reason() == {"kv_pressure": 3}


def test_cost_says_so_rather_than_quoting_a_number_it_cannot_support(tmp_path) -> None:
    text = Report.from_log(_log(tmp_path, [BUSY])).text()
    assert "not computed: no outcomes" in text


def test_goodput_counts_refusals_as_misses(tmp_path) -> None:
    """Definition 2: the denominator is everything OFFERED. Dividing by admitted
    instead would reward a policy for refusing more rather than refusing better."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, log=path) as p:
        p(IDLE, request_id="a")
        p.outcome("a", ttft_ms=100.0, ok=True)
        p(BUSY, request_id="b")  # refused, so never gets an outcome

    r = Report.from_log(path)
    assert "1 met / 2 offered" in r.text()
    assert "0.500" in r.text()


def test_sampled_enforcement_is_reported_as_both_sides(tmp_path) -> None:
    r = Report.from_log(_log(tmp_path, [BUSY] * 40, enforce=0.5))
    assert 0 < r.enforced < 40
    assert "holds both sides" in r.text()


# --- the instrument ------------------------------------------------------


def test_failed_scrapes_are_surfaced(tmp_path) -> None:
    """Otherwise the missing intervals read as a quiet cluster."""
    path = tmp_path / "t.jsonl"
    log = Log(path)
    log.write({"at": 1.0, "scrape_error": "connection refused"})
    log.close()
    assert "1 scrape(s) failed" in Report.from_log(path).text()


def test_a_policy_fault_is_surfaced(tmp_path) -> None:
    """A failed-open decision is the fallback, not the policy's judgement, and a run
    full of them must not read as healthy."""

    class Broken(Policy):
        name = "broken"

        def decide(self, metrics):
            raise ValueError("boom")

    path = tmp_path / "d.jsonl"
    with Broken(log=path) as p:
        p(BUSY, request_id="r1")
    assert "failed open" in Report.from_log(path).text()


def test_the_finding_comes_before_any_other_number(tmp_path) -> None:
    """A reader who stops after one line should still know whether the log proves
    anything."""
    text = Report.from_log(_log(tmp_path, [BUSY])).text()
    assert text.index("THE FINDING") < text.index("WHICH SIGNALS MOVED")
    assert text.index("WHICH SIGNALS MOVED") < text.index("WHAT IT COST")
