"""P0 — NoAdmission baseline. Every request is admitted."""

from __future__ import annotations

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState


class NoAdmission(AdmissionPolicy):
    name = "no_admission"

    # The baseline. It reads nothing and refuses nothing, so it has no signal
    # quantity and no threshold — and the report says so rather than treating the
    # absence as an inert signal.
    unit = "request"
    setting = "online"
    slo_awareness = "throughput"
    signal_quantity = None
    signal_structure = "scalar"
    metadata_assumed = "none"
    portability = "A"

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()
