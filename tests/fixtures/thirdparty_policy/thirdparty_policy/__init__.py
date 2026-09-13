"""A policy living entirely outside AdmitPerf, to prove spec D3.

Nothing in src/admitperf/ knows this exists. It is discovered only through the
`admitperf.policies` entry-point group declared in this package's pyproject.
"""

from __future__ import annotations

from admitperf import AdmissionPolicy, Decision, Request, SystemState


class RejectEverything(AdmissionPolicy):
    name = "third_party_reject_all"

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.reject(reason="third_party_says_no")
