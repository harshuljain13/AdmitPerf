"""P0 — NoAdmission baseline. Every request is admitted."""

from __future__ import annotations

from admitperf.policies.base import AdmissionPolicy, Decision, Request, SystemState


class NoAdmission(AdmissionPolicy):
    name = "no_admission"

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()
