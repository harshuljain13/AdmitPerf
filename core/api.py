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
    """

    name: str

    @abstractmethod
    def decide(self, req: Request, state: SystemState) -> Decision: ...

    def on_admit(self, req: Request, state: SystemState) -> None: ...
    def on_complete(self, req: Request, state: SystemState) -> None: ...
    def on_preempt(self, req: Request, state: SystemState) -> None: ...
