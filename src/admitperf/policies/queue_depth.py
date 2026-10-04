"""Refuse on queue length."""

from __future__ import annotations

from collections.abc import Mapping

from admitperf.core.decision import Decision
from admitperf.core.policy import Policy
from admitperf.core.signals import QUEUE_DEPTH


class QueueDepth(Policy):
    """Refuse when more than `max_waiting` requests are queued.

    Often the signal that actually moves. Queue depth responds to arrival rate
    directly, where KV pressure needs resident tokens — so on short requests this
    fires when a KV policy cannot.

    `retry_after_ms` turns the refusal into a defer, which tells a caller when to
    come back rather than only that it failed. Set it to 0 to refuse outright.
    """

    name = "queue_depth"

    def decide(self, metrics: Mapping[str, float]) -> Decision:
        waiting = QUEUE_DEPTH.read(metrics)
        if waiting is not None and waiting > self.max_waiting:
            retry = getattr(self, "retry_after_ms", 0)
            if retry:
                return self.defer("queue_depth", retry_after_ms=retry)
            return self.reject("queue_depth")
        return self.admit()
