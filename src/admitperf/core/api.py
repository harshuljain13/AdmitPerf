"""Adapter API v0 — the contract every AdmitPerf admission-control policy implements.

Frozen 2026-09-11 (Week 1 milestone). Any change here is a major-version bump.

Moved from admitperf/policies/base.py during the MVP package split (spec D7);
that path still re-exports these names for backward compatibility.

Note the deliberate omissions: this API carries no clock and no staleness field.
Time is injected via core.ports.Clock (spec D1), and snapshot age rides in
SystemState.engine_metrics under the reserved "admitperf:" prefix (spec D2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    ADMIT = "admit"
    DEFER = "defer"
    REJECT = "reject"


@dataclass(frozen=True)
class Decision:
    """A policy's per-request verdict.

    - ADMIT: request enters the engine immediately.
    - DEFER: hold the request; retry the policy after `retry_after_ms`.
    - REJECT: return an error to the caller with `reason`.
    """

    kind: DecisionKind
    reason: str | None = None
    retry_after_ms: int | None = None

    def __post_init__(self) -> None:
        if self.kind is DecisionKind.DEFER and self.retry_after_ms is None:
            raise ValueError("DEFER requires retry_after_ms")
        if self.kind is DecisionKind.REJECT and not self.reason:
            raise ValueError("REJECT requires a non-empty reason")

    @classmethod
    def admit(cls) -> Decision:
        return cls(DecisionKind.ADMIT)

    @classmethod
    def defer(cls, retry_after_ms: int, reason: str | None = None) -> Decision:
        return cls(DecisionKind.DEFER, reason=reason, retry_after_ms=retry_after_ms)

    @classmethod
    def reject(cls, reason: str) -> Decision:
        return cls(DecisionKind.REJECT, reason=reason)


@dataclass(frozen=True)
class Request:
    """One arriving unit — a single-turn request or one turn of an agent session."""

    request_id: str
    tenant_id: str
    arrival_time: float
    input_tokens: int
    agent_id: str | None = None
    expected_output_tokens: int | None = None
    deadline_ttft_ms: float | None = None
    deadline_tbt_ms: float | None = None
    priority: int = 0
    slo_class: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SystemState:
    """Snapshot of the serving system exposed to the policy at decision time.

    Populated by the engine adapter (vLLM / SGLang) from the engine's /metrics
    endpoint plus harness-side accounting. Values a given engine does not
    expose are `None`; policies must handle that gracefully.
    """

    now: float
    kv_used_fraction: float | None
    running_requests: int
    waiting_requests: int
    running_agents: int
    per_tenant_running: dict[str, int]
    per_tenant_admitted_recent: dict[str, int]
    engine_metrics: dict[str, float]


class AdmissionPolicy(ABC):
    """Abstract admission-control policy.

    A policy is a decision function: given an arriving request and current
    system state, return a Decision. Policies must be deterministic given
    the same inputs and internal state so experiments are reproducible.

    Declaring a policy
    ------------------
    Everything the report needs comes from class attributes, declared once::

        class KVThreshold(AdmissionPolicy):
            name = "kv_threshold"
            signal = "kv_used_fraction"
            threshold = 0.90

    `signal` is the field of `SystemState` this policy reads to decide. One
    declaration, four consumers: the capability check at wiring time, the
    liveness verdict, the Signal axis of the policy card, and the range table in
    the report. Naming it in four places is how they drift apart, and a drifted
    `requires` fails at wiring while a drifted report fails silently.

    `requires` is therefore DERIVED from `signal` unless a policy sets it
    explicitly — a policy reading two signals still can.
    """

    name: str

    # ----------------------------------------------------------------------
    # The survey's applicability columns. Six are declared here; the seventh,
    # signal liveness, is measured. Vocabularies live in report/taxonomy.py and
    # come from research/survey/docs/{taxonomy,applicability}.md.
    # ----------------------------------------------------------------------

    #: Axis 1. request | agent-session | tenant.
    unit: str = "request"

    #: Axis 2. online | offline | hybrid | theoretical.
    setting: str = "online"

    #: Axis 2 sub-branch. A continuous-batching server and a serial engine share
    #: almost no admission mechanism.
    concurrency_context: str = "concurrent-batch"

    #: Axis 3. What the policy protects: deadline | throughput | fairness | cost |
    #: stability | latency | reward. Named after the survey; this was once called
    #: `objective`, which appears nowhere in the taxonomy.
    slo_awareness: str = "deadline"

    #: Axis 3 sub-branches. Only set them when they apply.
    slo_granularity: str | None = None
    fairness_type: str | None = None

    #: Axis 4, the TAXONOMY term for what this policy watches: kv_pressure |
    #: queue_depth | deadline_slack | wait_estimate | predicted_length |
    #: batch_state | rate | analytic.
    #:
    #: Deliberately separate from `signal` below. The quantity is what makes two
    #: policies comparable across papers; the field is this implementation's
    #: plumbing. A card printing `kv_used_fraction` cannot be placed in the
    #: survey's table without a human translating it.
    signal_quantity: str | None = None

    #: Axis 4 sub-branch, on the STRUCTURE rather than the content:
    #:   scalar        one value against a threshold
    #:   dual-gate     two signals ANDed, to tell healthy high utilisation from
    #:                 congestion
    #:   lp-composite  a linear program over several state variables
    #:   formal-bound  a closed-form bound plus an admission test
    #:
    #: This decides what liveness can mean. A scalar has one range; a dual gate has
    #: two ranges and a conjunction, and can hold its first signal above threshold
    #: throughout while never firing.
    signal_structure: str = "scalar"

    #: What a request must carry for this policy to act at all: none | slo-class |
    #: deadlines | per-stage-deadlines | session-id | priority.
    #:
    #: Declared so a trace can be checked. A deadline policy run against requests
    #: with no deadlines admits everything and looks well-behaved, which is the
    #: quietest way to produce a meaningless run.
    metadata_assumed: str = "none"

    #: The `SystemState` field this policy reads to get its signal's value. Setting
    #: it is what makes the run reportable: without it the report can show that
    #: decisions were made but not whether the quantity behind them ever moved.
    signal: str | None = None

    #: The value of `signal` at which this policy changes its mind. Liveness is
    #: measured against it.
    threshold: float | None = None

    #: Derived from the decision moment: ingress -> A, queue-build or model-fork ->
    #: B. Class B needs control over batch formation and cannot sit behind this
    #: interface at all, so the default is the only value that can currently be true.
    portability: str = "A"

    requires: frozenset[str] = frozenset()

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Derive `requires` from `signal`, once, at class creation.

        Done here rather than as a property so that `requires` stays a plain
        frozenset for every existing caller, and so a policy that declares both
        keeps the explicit one.
        """
        super().__init_subclass__(**kwargs)
        declared = "requires" in cls.__dict__
        if cls.signal and not declared:
            cls.requires = frozenset({cls.signal})

    @abstractmethod
    def decide(self, req: Request, state: SystemState) -> Decision: ...

    def on_admit(self, req: Request, state: SystemState) -> None: ...
    def on_complete(self, req: Request, state: SystemState) -> None: ...
    def on_preempt(self, req: Request, state: SystemState) -> None: ...

    def read_signal(self, state: SystemState) -> float | None:
        """The value this policy's signal had at decision time.

        The runner calls this for every decision, so liveness is recorded whether
        or not a policy author thought about reporting. Returns None when the
        policy declares no signal, which the report then says out loud.

        The default reads `signal` as a field of `SystemState`, which covers any
        policy watching a raw telemetry value. A policy whose signal is COMPUTED —
        deadline slack, a schedulability margin, a predicted finish time — overrides
        this and returns its own number. `signal` is then just the name the report
        prints, and `requires` still lists the telemetry the computation needs.
        """
        if not self.signal:
            return None
        value = getattr(state, self.signal, None)
        return float(value) if isinstance(value, (int, float)) else None
