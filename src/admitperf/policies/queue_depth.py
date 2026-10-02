"""Reject when the engine's own queue is too deep.

Measured on a 0.5B model capped at `max_num_seqs=4`, `kv_cache_usage_perc`
never left 0.0 while `num_requests_waiting` climbed to 12. The KV cache was
orders of magnitude larger than four short sequences could fill, so the binding
constraint was the concurrency cap, not memory.

That is the case for this policy existing alongside `KVThreshold`: which signal
carries the pressure depends on the regime. KV pressure binds for long contexts
and large batches; queue depth binds when the concurrency cap is the ceiling. A
policy reading the wrong one sees a flat line and silently degrades into
admit-everything.
"""

from __future__ import annotations

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState


class QueueDepth(AdmissionPolicy):
    """Reject once more than `max_waiting` requests are already queued."""

    name = "queue_depth"
    requires = frozenset({"waiting_requests"})

    def __init__(self, max_waiting: int = 8) -> None:
        if max_waiting < 0:
            raise ValueError(f"max_waiting must be >= 0, got {max_waiting}")
        self.max_waiting = max_waiting

    def decide(self, req: Request, state: SystemState) -> Decision:
        if state.waiting_requests > self.max_waiting:
            return Decision.reject(reason="queue_depth")
        return Decision.admit()


class QueueDepthDefer(AdmissionPolicy):
    """Hold rather than refuse, and re-ask once the queue has drained.

    The interesting contrast with `QueueDepth`: deferring converts a refusal
    into latency. Whether that is an improvement depends entirely on whether
    the client would rather wait than be told no, which is what the results are
    for.
    """

    name = "queue_depth_defer"
    requires = frozenset({"waiting_requests"})

    def __init__(self, max_waiting: int = 8, retry_after_ms: int = 250) -> None:
        self.max_waiting = max_waiting
        self.retry_after_ms = retry_after_ms

    def decide(self, req: Request, state: SystemState) -> Decision:
        if state.waiting_requests > self.max_waiting:
            return Decision.defer(retry_after_ms=self.retry_after_ms, reason="queue_depth")
        return Decision.admit()
