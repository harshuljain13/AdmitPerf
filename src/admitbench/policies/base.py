"""Base interface every admission-control policy implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AdmissionDecision(str, Enum):
    ADMIT = "admit"
    QUEUE = "queue"
    REJECT = "reject"


@dataclass(frozen=True)
class Request:
    """One arriving unit — a single-turn request or one turn of an agent session."""

    request_id: str
    agent_id: str | None
    tenant_id: str
    arrival_time: float
    input_tokens: int
    expected_output_tokens: int | None
    deadline_ttft: float | None
    deadline_tbt: float | None
    priority: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class SystemState:
    """Snapshot of the serving system exposed to the policy at decision time.

    Populated by the harness from the engine's /metrics endpoint and its own
    accounting. Values that a given engine does not expose are None.
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

    A policy is a pure decision function: given the arriving request and current
    system state, return ADMIT / QUEUE / REJECT. Policies must be deterministic
    given the same inputs and internal state to keep experiments reproducible.
    """

    name: str

    @abstractmethod
    def decide(self, req: Request, state: SystemState) -> AdmissionDecision: ...

    def on_admit(self, req: Request, state: SystemState) -> None:
        """Callback when a request is admitted. Update policy state here."""

    def on_complete(self, req: Request, state: SystemState) -> None:
        """Callback when a request finishes. Update policy state here."""

    def on_preempt(self, req: Request, state: SystemState) -> None:
        """Callback when a request is preempted mid-flight."""
