"""Refuse above a KV cache threshold."""

from __future__ import annotations

from collections.abc import Mapping

from admitperf.core.decision import Decision
from admitperf.core.policy import Policy
from admitperf.core.signals import KV_PRESSURE


class KvThreshold(Policy):
    """Refuse when the KV cache is fuller than `threshold`.

    The simplest thing that works, and the one most likely to measure nothing: KV
    pressure is a function of resident tokens, so a small model or short requests
    leave it flat near zero while the queue grows. The signal then never reaches the
    threshold, the policy never fires, and the run reports numbers indistinguishable
    from no policy at all.

    `KV_PRESSURE.read` returns None when nothing supplies it, so that case admits
    rather than crashing — and the log records the signal as absent, which is how the
    report can tell "never fired" from "could not see".
    """

    name = "kv_threshold"

    def decide(self, metrics: Mapping[str, float]) -> Decision:
        pressure = KV_PRESSURE.read(metrics)
        if pressure is not None and pressure >= self.threshold:
            return self.reject("kv_pressure")
        return self.admit()
