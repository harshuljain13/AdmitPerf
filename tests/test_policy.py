"""Policy — recording that cannot be forgotten, in a path that cannot break."""

from __future__ import annotations

import pytest

from admitperf.core.log import Log
from admitperf.core.policy import Policy
from admitperf.core.signals import KV_PRESSURE, QUEUE_DEPTH
from admitperf.core.verdict import Verdict

SCRAPE = {
    "vllm:kv_cache_usage_perc": 0.93,
    "vllm:num_requests_waiting": 14.0,
    "vllm:num_requests_running": 61.0,
    "DCGM_FI_DEV_GPU_UTIL": 94.0,
}


class KvWall(Policy):
    name = "kv_wall"

    def decide(self, metrics):
        if KV_PRESSURE.read(metrics) >= self.threshold:
            return self.reject("kv_pressure")
        if QUEUE_DEPTH.read(metrics) > self.max_waiting:
            return self.defer("queue_depth", retry_after_ms=200)
        return self.admit()


# --------------------------------------------------------------------------
# Recording is not opt-in
# --------------------------------------------------------------------------


def test_every_call_is_recorded_without_being_asked(tmp_path) -> None:
    """Subclassing is the opt-in. There is no instrumented and uninstrumented version
    to choose between, and no second object to construct."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path) as p:
        p(SCRAPE, request_id="r1")
        p({**SCRAPE, "vllm:kv_cache_usage_perc": 0.1}, request_id="r2")
    assert len(Log.read(path)) == 2


def test_the_record_holds_every_signal_not_just_the_one_read(tmp_path) -> None:
    """The asymmetry everything rests on: decide on a subset, record all of it.

    Without this a report cannot say "your signal never moved but queue depth hit
    61", and replaying a different policy over the same log is impossible.
    """
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path) as p:
        p(SCRAPE, request_id="r1")

    rec = Log.read(path)[0]
    assert rec["signals"]["kv_pressure"] == 0.93
    assert rec["signals"]["queue_depth"] == 14.0  # not read on the reject path
    assert rec["signals"]["gpu_util"] == pytest.approx(0.94)
    assert rec["signals"]["prefix_hit_rate"] is None  # absent, not zero
    assert rec["metrics"] == SCRAPE  # and the raw bag, verbatim


def test_the_record_says_how_each_signal_was_obtained(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path) as p:
        p(SCRAPE, request_id="r1")
    assert Log.read(path)[0]["sources"]["kv_pressure"] == "vllm:kv_cache_usage_perc"


def test_the_record_carries_the_parameters_used(tmp_path) -> None:
    """A verdict without the threshold behind it cannot be checked by anyone."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.77, max_waiting=8, log=path) as p:
        p(SCRAPE, request_id="r1")
    assert Log.read(path)[0]["params"] == {"threshold": 0.77, "max_waiting": 8}


def test_an_outcome_links_to_its_request(tmp_path) -> None:
    """Without outcomes a report says what the policy DID and never what it BOUGHT:
    no latency, no goodput."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path) as p:
        p({**SCRAPE, "vllm:kv_cache_usage_perc": 0.1}, request_id="r2")
        p.outcome("r2", ttft_ms=418.0, ok=True)
    recs = Log.read(path)
    assert recs[1]["request_id"] == "r2"
    assert recs[1]["outcome"] == {"ttft_ms": 418.0, "ok": True}


def test_no_log_means_no_file(tmp_path) -> None:
    KvWall(threshold=0.9, max_waiting=32)(SCRAPE, request_id="r1")
    assert not list(tmp_path.iterdir())


# --------------------------------------------------------------------------
# The host must never break
# --------------------------------------------------------------------------


def test_a_policy_that_raises_fails_open_and_records_the_fault(tmp_path) -> None:
    """Failing closed on a bug sheds ALL traffic, which is worse than the bug. The
    fault is recorded so the run is not silently reported as healthy."""

    class Broken(Policy):
        name = "broken"

        def decide(self, metrics):
            raise ZeroDivisionError("oops")

    path = tmp_path / "d.jsonl"
    with Broken(log=path) as p:
        d = p(SCRAPE, request_id="r1")

    assert d.admitted
    assert "ZeroDivisionError" in Log.read(path)[0]["fault"]


def test_a_host_can_choose_to_fail_closed() -> None:
    """Some deployments would rather shed than admit on a bug. The refusal still lands
    in the vocabulary, so it is groupable in a report."""

    class Strict(Policy):
        name = "strict"
        on_error = Verdict.REJECT

        def decide(self, metrics):
            raise RuntimeError("nope")

    d = Strict()(SCRAPE, request_id="r1")
    assert not d.admitted
    assert (d.reason, d.status) == ("policy_error", 503)


def test_a_missing_signal_does_not_crash_a_shipped_policy() -> None:
    """An engine that exposes no KV gauge should degrade to admitting, with the log
    showing the signal absent — which is how a report tells "never fired" from
    "could not see"."""
    from admitperf.policies import KvThreshold

    assert KvThreshold(threshold=0.9)({"unrelated": 1.0}, request_id="r1").admitted


# --------------------------------------------------------------------------
# Sampled enforcement
# --------------------------------------------------------------------------


def test_unenforced_requests_are_measured_but_not_governed(tmp_path) -> None:
    """One run yields both policies under identical conditions. Two sequential runs cannot:
    they share an engine, so the second starts against the first's leftover queue."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path, enforce=0.5) as p:
        admitted = sum(p(SCRAPE, request_id=f"r{i}").admitted for i in range(200))

    recs = Log.read(path)
    enforced = [r for r in recs if r["enforced"]]
    assert 0 < len(enforced) < 200
    # The policy's real verdict is recorded either way, so the report can compare.
    assert all(r["verdict"] == "reject" for r in recs)
    assert admitted == 200 - len(enforced)


def test_the_split_is_deterministic_so_a_retry_is_treated_the_same() -> None:
    """Random sampling would let a client succeed by retrying past a refusal, and
    would make a replay irreproducible."""
    p = KvWall(threshold=0.9, max_waiting=32, enforce=0.5)
    first = [p(SCRAPE, request_id=f"r{i}").admitted for i in range(50)]
    again = [p(SCRAPE, request_id=f"r{i}").admitted for i in range(50)]
    assert first == again


def test_enforce_zero_is_shadow_mode(tmp_path) -> None:
    """Not a flag: recording is unconditional and enforcement is the host's, so
    nothing is shed while everything is measured."""
    path = tmp_path / "d.jsonl"
    with KvWall(threshold=0.9, max_waiting=32, log=path, enforce=0.0) as p:
        assert all(p(SCRAPE, request_id=f"r{i}").admitted for i in range(10))
    recs = Log.read(path)
    assert len(recs) == 10
    assert all(r["verdict"] == "reject" and not r["enforced"] for r in recs)


def test_an_out_of_range_enforce_is_refused() -> None:
    with pytest.raises(ValueError, match=r"enforce must be in \[0, 1\]"):
        KvWall(threshold=0.9, max_waiting=32, enforce=1.5)


# --------------------------------------------------------------------------
# Ergonomics
# --------------------------------------------------------------------------


def test_params_are_plain_attributes() -> None:
    """`self.threshold`, not `self.p.threshold`."""
    p = KvWall(threshold=0.77, max_waiting=8)
    assert (p.threshold, p.max_waiting) == (0.77, 8)


def test_a_host_metric_is_reachable_without_a_signal() -> None:
    """Your own metric stays yours: not comparable across deployments, which is
    honest, and needing no registration of any kind."""

    class TenantFair(Policy):
        name = "tenant_fair"

        def decide(self, metrics):
            if metrics["acme:tenant_tokens_per_min"] > 10_000:
                return self.reject("tenant_quota")
            return self.admit()

    d = TenantFair()({"acme:tenant_tokens_per_min": 99_000}, request_id="r1")
    assert d.status == 429


def test_a_policy_without_a_name_is_refused() -> None:
    class Nameless(Policy):
        def decide(self, metrics):
            return self.admit()

    with pytest.raises(ValueError, match="needs a `name`"):
        Nameless()
