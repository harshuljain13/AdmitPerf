"""A named, comparable set of arms."""

from __future__ import annotations

from dataclasses import dataclass, field

from admitperf.policy_runs import PolicyRuns


@dataclass
class Experiment:
    """One question, and the policies measured to answer it.

    The name comes from whoever ran it — `Policy(experiment="kv-wall-8k")` — and never
    from the directory the logs landed in. That matters because grouping IS the claim:
    two policies in the same experiment are asserted to have faced the same conditions, and
    inferring that from a folder means moving a file silently changes what the result
    says.
    """

    name: str
    #: By policy name, because that is how a reader asks for one.
    policies: dict[str, PolicyRuns] = field(default_factory=dict)
    notes: str | None = None

    @property
    def baseline(self) -> PolicyRuns | None:
        """The arm that admits everything.

        Without it there is nothing to compare against, and "the policy refused 27%"
        has nothing to be 27% of.
        """
        return next((p for p in self.policies.values() if p.baseline), None)

    @property
    def candidates(self) -> list[PolicyRuns]:
        """Everything measured against the baseline."""
        return [p for p in self.policies.values() if not p.baseline]

    @property
    def runs(self) -> int:
        return sum(len(p.logs) for p in self.policies.values())
