"""The policy card — the survey's five axes, read off the policy itself.

A policy declares these as class attributes, so the card fills itself in. The
alternative is a human writing the card at paper-writing time, which is exactly
how the corpus ended up with sixteen policies and no shared vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from admitperf.core.api import AdmissionPolicy

#: The vocabulary. Values outside these sets are allowed but flagged, because a
#: new value is either a real extension of the taxonomy or a typo, and the report
#: should not quietly decide which.
VOCABULARY: dict[str, frozenset[str]] = {
    "unit": frozenset({"request", "batch", "token", "session"}),
    "setting": frozenset({"online", "offline"}),
    "objective": frozenset({"deadline", "latency", "throughput", "fairness", "cost"}),
    "portability": frozenset({"A", "B"}),
}

AXIS_HELP: dict[str, str] = {
    "unit": "what the policy decides about",
    "setting": "whether it decides as requests arrive, or in advance",
    "objective": "what it is trying to protect",
    "signal": "the field of SystemState it reads",
    "portability": "A = pure function of (request, state); B = needs engine changes",
}


@dataclass(frozen=True)
class PolicyCard:
    """What a policy says about itself."""

    name: str
    unit: str
    setting: str
    objective: str
    signal: str | None
    threshold: float | None
    portability: str
    requires: frozenset[str]

    @classmethod
    def of(cls, policy: AdmissionPolicy) -> PolicyCard:
        """Read the card off a live policy instance.

        Instance attributes win over class attributes, so a policy constructed
        with `KVThreshold(threshold=0.7)` reports 0.7 rather than the default.
        That matters: a card quoting the default while the run used something
        else is worse than no card.
        """
        return cls(
            name=getattr(policy, "name", type(policy).__name__),
            unit=policy.unit,
            setting=policy.setting,
            objective=policy.objective,
            signal=policy.signal,
            threshold=(float(policy.threshold) if policy.threshold is not None else None),
            portability=policy.portability,
            requires=frozenset(getattr(policy, "requires", frozenset())),
        )

    def anomalies(self) -> list[str]:
        """Everything about this card a reader should not have to notice themselves."""
        out: list[str] = []
        for axis in ("unit", "setting", "objective", "portability"):
            value = getattr(self, axis)
            if value not in VOCABULARY[axis]:
                out.append(
                    f"{axis}={value!r} is outside the taxonomy "
                    f"({', '.join(sorted(VOCABULARY[axis]))})"
                )
        if self.signal is None:
            out.append(
                "no signal declared, so liveness cannot be measured and this run "
                "cannot show whether the policy had the opportunity to act"
            )
        elif self.threshold is None:
            out.append(
                f"signal {self.signal!r} declared without a threshold, so its range "
                "can be reported but not judged"
            )
        if self.signal and self.signal not in self.requires:
            out.append(
                f"signal {self.signal!r} is not in requires={sorted(self.requires)}; "
                "the capability check will not catch an engine that cannot supply it"
            )
        return out
