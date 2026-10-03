"""The policy card — the survey's applicability columns, read off the policy.

Source of truth: `research/survey/docs/taxonomy.md` (four axes) and
`docs/applicability.md` (the seven columns of the Tier 1 table). Every vocabulary
here is the survey's. Where this file once invented values — `batch` and `token` as
admission units, `objective` for SLO-awareness — that was a defect, because a card
whose terms differ from the survey's cannot be placed in the table it exists to fill.

The survey's finding is that six of the seven columns are fully recoverable from
published text and the seventh is uniformly empty: of sixteen admission-primary
papers, not one reports the observed range of the quantity its policy reads. A
policy declares the six; AdmitPerf measures the seventh.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from admitperf.core.api import AdmissionPolicy

# --------------------------------------------------------------------------
# Axis 1 — unit of admission
# --------------------------------------------------------------------------
#: taxonomy.md Axis 1. `batch` and `token` are NOT admission units: batch formation
#: is what a Class B policy controls, not what it admits.
UNITS = frozenset({"request", "agent-session", "tenant"})

# --------------------------------------------------------------------------
# Axis 2 — setting
# --------------------------------------------------------------------------
SETTINGS = frozenset({"online", "offline", "hybrid", "theoretical"})

#: Axis 2 sub-branch. Server and edge admission share almost no mechanism: a
#: predictive-SJF policy works on a serial engine and would need different plumbing
#: on a continuous-batching one.
CONCURRENCY_CONTEXTS = frozenset({"concurrent-batch", "serial-edge"})

# --------------------------------------------------------------------------
# Axis 3 — SLO awareness
# --------------------------------------------------------------------------
#: What the policy is trying to protect. Named `slo_awareness` after the survey;
#: this file previously called it `objective`, which appears nowhere in the taxonomy.
SLO_AWARENESS = frozenset(
    {"deadline", "throughput", "fairness", "cost", "stability", "latency", "reward"}
)

#: Axis 3 sub-branch. Per-stage is a genuine taxonomy gap with a single occupant, so
#: a card that cannot express it cannot describe that policy at all.
SLO_GRANULARITIES = frozenset({"job", "request", "per-stage"})

#: Axis 3 sub-branch. Task-fairness and tenant-fairness share almost no mechanism,
#: and conflating them would mislead a reader looking for multi-tenant isolation.
FAIRNESS_TYPES = frozenset({"task", "tenant", "priority-weighted"})

# --------------------------------------------------------------------------
# Axis 4 — signal
# --------------------------------------------------------------------------
#: The quantity compared against a threshold. applicability.md's controlled
#: vocabulary. This is the TAXONOMY term and is not the same thing as the
#: `SystemState` field a policy reads — see AdmissionPolicy.signal_field.
SIGNAL_QUANTITIES = frozenset(
    {
        "kv_pressure",
        "queue_depth",
        "deadline_slack",
        "wait_estimate",
        "predicted_length",
        "batch_state",
        "rate",
        "analytic",
    }
)

#: Axis 4 sub-branches, on the STRUCTURE of the signal rather than its content. This
#: matters for what liveness can even mean: a scalar has one range, a dual gate has
#: two ranges and a conjunction, an LP has a feasibility region.
SIGNAL_STRUCTURES = frozenset({"scalar", "dual-gate", "lp-composite", "formal-bound"})

#: Structures AdmitPerf's single-scalar liveness check can actually judge. Anything
#: else gets its range reported and its verdict withheld, because a confident verdict
#: about a firing condition the tool cannot express is worse than no verdict.
JUDGEABLE_STRUCTURES = frozenset({"scalar"})

# --------------------------------------------------------------------------
# Request metadata
# --------------------------------------------------------------------------
#: What a request must carry for the policy to act at all. `none` means the policy
#: needs only the request and the system state. Eight of the survey's sixteen are
#: `none`; the rest need something the workload has to supply, and a policy run
#: against a trace lacking it will admit everything and look well-behaved.
METADATA = frozenset(
    {"none", "slo-class", "deadlines", "per-stage-deadlines", "session-id", "priority"}
)

#: Which Request field satisfies each metadata requirement, so a trace can be
#: checked rather than assumed.
METADATA_FIELD = {
    "slo-class": "slo_class",
    "deadlines": "deadline_ttft_ms",
    "per-stage-deadlines": "deadline_ttft_ms",
    "session-id": "agent_id",
    "priority": "priority",
}

# --------------------------------------------------------------------------
# Portability
# --------------------------------------------------------------------------
#: Derived from the decision moment: ingress -> A, queue-build or model-fork -> B.
#: A Class B policy cannot sit behind this interface at all, which is the
#: operational consequence and worth catching before a port is attempted.
PORTABILITIES = frozenset({"A", "B"})

VOCABULARY: dict[str, frozenset[str]] = {
    "unit": UNITS,
    "setting": SETTINGS,
    "concurrency_context": CONCURRENCY_CONTEXTS,
    "slo_awareness": SLO_AWARENESS,
    "slo_granularity": SLO_GRANULARITIES,
    "fairness_type": FAIRNESS_TYPES,
    "signal_quantity": SIGNAL_QUANTITIES,
    "signal_structure": SIGNAL_STRUCTURES,
    "metadata_assumed": METADATA,
    "portability": PORTABILITIES,
}

AXIS_HELP: dict[str, str] = {
    "unit": "what the policy decides about",
    "setting": "whether it decides as requests arrive, or in advance",
    "concurrency_context": "continuous-batching server, or a serial engine",
    "slo_awareness": "what it is trying to protect",
    "slo_granularity": "one SLO per job, per request, or per stage",
    "fairness_type": "between task types, between tenants, or weighted classes",
    "signal_quantity": "the quantity it compares against a threshold",
    "signal_structure": "scalar, two signals ANDed, an LP, or a formal bound",
    "metadata_assumed": "what a request must carry for the policy to act",
    "portability": "A = decidable at ingress; B = needs control of batch formation",
}

#: The columns of the survey's Tier 1 applicability table, in order. Six are declared
#: by the policy; the seventh is measured.
COLUMNS = (
    "unit",
    "setting",
    "slo_awareness",
    "signal_quantity",
    "metadata_assumed",
    "portability",
)


@dataclass(frozen=True)
class PolicyCard:
    """What a policy says about itself, in the survey's terms."""

    name: str
    unit: str
    setting: str
    slo_awareness: str
    signal_quantity: str | None
    signal_structure: str
    signal_field: str | None
    threshold: float | None
    metadata_assumed: str
    portability: str
    requires: frozenset[str]
    concurrency_context: str = "concurrent-batch"
    slo_granularity: str | None = None
    fairness_type: str | None = None

    @classmethod
    def of(cls, policy: AdmissionPolicy) -> PolicyCard:
        """Read the card off a live policy instance.

        Instance attributes win over class attributes, so a policy constructed with
        `KVThreshold(threshold=0.7)` reports 0.7. A card quoting the default while
        the run used something else is worse than no card.
        """
        return cls(
            name=getattr(policy, "name", type(policy).__name__),
            unit=policy.unit,
            setting=policy.setting,
            slo_awareness=policy.slo_awareness,
            signal_quantity=policy.signal_quantity,
            signal_structure=policy.signal_structure,
            signal_field=policy.signal,
            threshold=(float(policy.threshold) if policy.threshold is not None else None),
            metadata_assumed=policy.metadata_assumed,
            portability=policy.portability,
            requires=frozenset(getattr(policy, "requires", frozenset())),
            concurrency_context=policy.concurrency_context,
            slo_granularity=policy.slo_granularity,
            fairness_type=policy.fairness_type,
        )

    @property
    def liveness_is_judgeable(self) -> bool:
        """Whether a single scalar range can describe this policy's firing condition.

        False for a dual gate, an LP or a formal bound. For those the range is still
        worth reporting, but calling it a liveness verdict would assert something the
        check cannot see — a dual-gate policy can have its first signal above
        threshold throughout while the conjunction never holds.
        """
        return self.signal_structure in JUDGEABLE_STRUCTURES

    def anomalies(self) -> list[str]:
        """Everything about this card a reader should not have to notice themselves."""
        out: list[str] = []
        for axis in (
            "unit",
            "setting",
            "concurrency_context",
            "slo_awareness",
            "signal_structure",
            "metadata_assumed",
            "portability",
        ):
            value = getattr(self, axis)
            if value and value not in VOCABULARY[axis]:
                out.append(
                    f"{axis}={value!r} is outside the survey's vocabulary "
                    f"({', '.join(sorted(VOCABULARY[axis]))})"
                )
        for axis in ("signal_quantity", "slo_granularity", "fairness_type"):
            value = getattr(self, axis)
            if value and value not in VOCABULARY.get(axis, frozenset()):
                out.append(
                    f"{axis}={value!r} is outside the survey's vocabulary "
                    f"({', '.join(sorted(VOCABULARY[axis]))})"
                )

        if self.signal_quantity is None:
            out.append(
                "no signal_quantity declared, so this policy cannot be placed on the "
                "survey's axis 4 and its liveness cannot be measured"
            )
        elif self.threshold is None and self.signal_structure == "scalar":
            out.append(
                f"signal_quantity {self.signal_quantity!r} is scalar but no threshold "
                "is declared, so its range can be reported and not judged"
            )
        if self.signal_field and self.signal_field not in self.requires:
            out.append(
                f"signal_field {self.signal_field!r} is not in "
                f"requires={sorted(self.requires)}; the capability check will not "
                "catch an engine that cannot supply it"
            )
        if not self.liveness_is_judgeable:
            out.append(
                f"signal_structure is {self.signal_structure!r}: the range below is "
                "one component, and a single-scalar liveness check cannot describe "
                "this policy's firing condition. No verdict is claimed"
            )
        if self.portability == "B":
            out.append(
                "Class B: this policy needs control over batch formation, so it "
                "cannot sit behind this interface in front of an unmodified engine"
            )
        if self.slo_awareness == "fairness" and not self.fairness_type:
            out.append(
                "fairness without a fairness_type: task-fairness and tenant-fairness "
                "share almost no mechanism, and conflating them misleads a reader "
                "looking for multi-tenant isolation"
            )
        return out

    def missing_metadata(self, sample: object) -> str | None:
        """Whether a request carries what this policy needs, or what is absent.

        A deadline policy run against a trace with no deadlines admits everything and
        looks well-behaved, which is the quietest way to produce a meaningless run.
        """
        if self.metadata_assumed in ("none", ""):
            return None
        field = METADATA_FIELD.get(self.metadata_assumed)
        if not field:
            return None
        if getattr(sample, field, None) in (None, ""):
            return (
                f"policy assumes {self.metadata_assumed!r}, but requests carry no "
                f"{field}. It will admit everything and look well-behaved"
            )
        return None
