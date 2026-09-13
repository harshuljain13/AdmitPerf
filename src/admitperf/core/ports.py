"""The two objects the frozen API is missing, and the one interface with more
than one implementation.

Deliberately small. An interface earns its place when something real is going
to be swapped behind it — engines are (vLLM today, SGLang next), so
`EngineAdapter` exists. Everything else here is a plain dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from admitperf.core.api import Request, SystemState

#: How old the scraped snapshot was when a decision was made. The frozen API
#: has no field for it, so it rides in SystemState.engine_metrics under a
#: namespaced key.
STATE_AGE_KEY = "admitperf:state_age_s"


@dataclass(frozen=True)
class RequestOutcome:
    """What happened to an admitted request.

    Timed on the client side, as the response streams back. This is not a
    stylistic choice: vLLM's Prometheus histograms are fleet-wide sum/count
    counters, so they give a mean across every request the server has ever
    seen and cannot be attributed to one request, let alone to one admission
    decision.
    """

    request_id: str
    status: str  # "completed" | "failed"
    ttft_ms: float | None = None
    tbt_ms: tuple[float, ...] = ()
    total_ms: float | None = None
    output_tokens: int = 0
    met_deadline: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """One admission decision, with the state it was made on.

    Keeping the state alongside the verdict is what makes a run explainable
    afterwards: "why was this rejected" is answerable only if the KV pressure
    and queue depth at that instant were written down with it.
    """

    request_id: str
    tenant_id: str
    decided_at: float
    kind: str
    reason: str | None
    http_status: int | None
    state_age_s: float | None
    kv_used_fraction: float | None
    waiting_requests: int
    running_requests: int


@runtime_checkable
class EngineAdapter(Protocol):
    """A serving engine AdmitPerf can drive: vLLM now, SGLang next."""

    name: str

    async def fetch_state(self) -> SystemState:
        """Scrape the engine's own telemetry into a snapshot."""
        ...

    async def submit(self, req: Request) -> RequestOutcome:
        """Run one request, timing the response stream."""
        ...

    def capabilities(self) -> frozenset[str]:
        """Which SystemState fields this engine can actually populate."""
        ...

    async def aclose(self) -> None: ...


@dataclass
class CapabilityError(Exception):
    """A policy needs a signal this engine cannot provide.

    Raised once at startup rather than per request. Without the check, a policy
    reading a missing kv_used_fraction as 0.0 would conclude the cache is empty
    and admit everything, and the run would look successful while measuring
    nothing.
    """

    policy: str
    engine: str
    missing: frozenset[str] = field(default_factory=frozenset)

    def __str__(self) -> str:
        want = ", ".join(sorted(self.missing))
        return (
            f"policy {self.policy!r} requires [{want}], "
            f"which engine {self.engine!r} does not provide"
        )


def check_compatibility(
    policy_name: str,
    requires: frozenset[str],
    engine_name: str,
    provides: frozenset[str],
) -> None:
    missing = requires - provides
    if missing:
        raise CapabilityError(policy=policy_name, engine=engine_name, missing=missing)


__all__ = [
    "STATE_AGE_KEY",
    "CapabilityError",
    "DecisionRecord",
    "EngineAdapter",
    "RequestOutcome",
    "check_compatibility",
]
