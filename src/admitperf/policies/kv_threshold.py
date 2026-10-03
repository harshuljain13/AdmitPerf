"""KV-threshold policy — the simplest thing that can reject.

Exists to exercise the REJECT path end to end (task 2.6) and to be the first
policy that *declares a requirement*: it is useless without KV telemetry, so it
says so via `requires` and fails at wiring time rather than silently treating a
missing signal as "cache empty" (spec D8).
"""

from __future__ import annotations

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState


class KVThreshold(AdmissionPolicy):
    """Reject when KV cache utilization is at or above `threshold`."""

    name = "kv_threshold"

    # The survey's applicability columns.
    unit = "request"
    setting = "online"
    slo_awareness = "deadline"
    signal_quantity = "kv_pressure"
    signal_structure = "scalar"
    metadata_assumed = "none"
    portability = "A"

    # Implementation: which SystemState field carries the quantity, and the value
    # at which this policy changes its mind.
    signal = "kv_used_fraction"
    threshold = 0.90

    def __init__(self, threshold: float = 0.90) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold}")
        self.threshold = threshold

    def decide(self, req: Request, state: SystemState) -> Decision:
        kv = state.kv_used_fraction
        if kv is None:
            # Unreachable when capabilities are checked at wiring time; kept so
            # the policy is still correct if constructed directly in a test.
            return Decision.admit()
        if kv >= self.threshold:
            return Decision.reject(reason="kv_pressure")
        return Decision.admit()
