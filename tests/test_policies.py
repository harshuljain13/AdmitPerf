"""The policies AdmitPerf ships."""

from __future__ import annotations

from admitperf.policies import DualGate, KvThreshold, NoAdmission, QueueDepth

BUSY = {
    "vllm:kv_cache_usage_perc": 0.95,
    "vllm:num_requests_waiting": 40.0,
    "vllm:prefix_cache_hits_total": 50.0,
    "vllm:prefix_cache_queries_total": 1000.0,
}
IDLE = {"vllm:kv_cache_usage_perc": 0.05, "vllm:num_requests_waiting": 0.0}


def test_no_admission_admits_everything() -> None:
    """Not a placeholder: without it "refused 8%" has nothing to be 8% of."""
    assert NoAdmission()(BUSY, request_id="r").admitted


def test_kv_threshold_fires_above_and_not_below() -> None:
    p = KvThreshold(threshold=0.90)
    assert p(BUSY, request_id="r1").reason == "kv_pressure"
    assert p(IDLE, request_id="r2").admitted


def test_queue_depth_rejects_by_default_and_defers_when_told() -> None:
    """A defer tells a caller when to come back rather than only that it failed."""
    assert QueueDepth(max_waiting=32)(BUSY, request_id="r").status == 503
    d = QueueDepth(max_waiting=32, retry_after_ms=200)(BUSY, request_id="r")
    assert (d.verdict.value, d.retry_after_ms) == ("defer", 200)


def test_a_dual_gate_holds_above_threshold_without_firing() -> None:
    """The reason signal STRUCTURE is an axis in the survey: a dual gate can sit above
    its first threshold for an entire run and never fire, so reporting one signal's
    range says nothing about whether the policy acted.
    """
    p = DualGate(threshold=0.90, min_hit_rate=0.30)
    # cache full, reuse poor -> congestion, refuse
    assert p(BUSY, request_id="r1").reason == "kv_pressure"
    # cache equally full, reuse high -> the cache is doing its job, admit
    reusing = {**BUSY, "vllm:prefix_cache_hits_total": 900.0}
    assert p(reusing, request_id="r2").admitted


def test_a_dual_gate_admits_when_either_signal_is_missing() -> None:
    """Half a gate is not a gate. Refusing on one leg would make the policy stricter
    than it was configured to be, which is the opposite of safe."""
    assert DualGate(threshold=0.90, min_hit_rate=0.30)(IDLE, request_id="r").admitted
