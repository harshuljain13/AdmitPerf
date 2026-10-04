"""The baseline."""

from __future__ import annotations

from collections.abc import Mapping

from admitperf.core.decision import Decision
from admitperf.core.policy import Policy


class NoAdmission(Policy):
    """Admits everything.

    Not a placeholder. Without it "the policy refused 8%" has nothing to be 8% of,
    and any latency figure is uninterpretable. It is also what shows
    your cluster does when nothing is managing it, which is the comparison every
    claim about admission control rests on.
    """

    name = "no_admission"

    #: Refusing nothing is the job, not a failure to fire. Without this the report
    #: would call the baseline INERT and advise changing the load.
    baseline = True

    def decide(self, metrics: Mapping[str, float]) -> Decision:
        return self.admit()
