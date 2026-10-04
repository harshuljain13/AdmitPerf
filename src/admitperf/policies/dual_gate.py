"""Two signals, ANDed."""

from __future__ import annotations

from collections.abc import Mapping

from admitperf.core.decision import Decision
from admitperf.core.policy import Policy
from admitperf.core.signals import KV_PRESSURE, PREFIX_HIT_RATE


class DualGate(Policy):
    """Refuse only when the cache is full AND reuse is poor.

    High utilisation on its own is not congestion — a cache full of shared prefixes
    is a cache doing its job. Refusing on utilisation alone sheds traffic during
    exactly the conditions you built the cache for.

    This is why the survey treats signal STRUCTURE as an axis: a dual gate can hold
    its first signal above threshold for an entire run and still never fire, so
    reporting the range of one signal says nothing about whether the policy acted.
    """

    name = "dual_gate"

    def decide(self, metrics: Mapping[str, float]) -> Decision:
        pressure = KV_PRESSURE.read(metrics)
        reuse = PREFIX_HIT_RATE.read(metrics)
        if pressure is None or reuse is None:
            return self.admit()
        if pressure >= self.threshold and reuse < self.min_hit_rate:
            return self.reject("kv_pressure")
        return self.admit()
