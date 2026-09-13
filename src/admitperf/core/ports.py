"""Ports — the protocols every AdmitPerf component is injected as.

Nothing here is an implementation. `Runner` depends on these and only these, so
a replay engine and a live vLLM fleet are interchangeable (spec D4), and so
neither the benchmark harness nor the runtime middleware is a required import
for the other (spec D7).

These are *not* part of frozen adapter API v0 — that is `core.api`, and it does
not change. These ports are MVP-owned and may evolve.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from admitperf.core.api import Decision, Request, SystemState

# Reserved keys AdmitPerf writes into SystemState.engine_metrics (spec D2).
# The frozen API has no field for these, so they ride in the free-form dict
# under a namespaced prefix. If this set keeps growing, that is the signal to
# cut API v1 with real fields rather than keep overloading a dict.
STATE_AGE_KEY = "admitperf:state_age_s"

# Manifest key, not a SystemState key: engine_metrics is typed float-only, and
# the engine identity belongs to the run rather than to any single snapshot.
ENGINE_KEY = "engine"


@dataclass(frozen=True)
class RequestOutcome:
    """What actually happened to an admitted request.

    Timings here are **client-side** (spec D5): measured from the streaming
    response, because engine Prometheus histograms are fleet-aggregate
    sum/count counters and cannot be attributed to an individual request.
    """

    request_id: str
    status: str  # "completed" | "failed" | "preempted"
    ttft_ms: float | None = None
    tbt_ms: tuple[float, ...] = ()
    total_ms: float | None = None
    output_tokens: int = 0
    met_deadline: bool | None = None
    error: str | None = None


@dataclass(frozen=True)
class DecisionRecord:
    """One admission decision, as written to decisions.jsonl."""

    request_id: str
    tenant_id: str
    decided_at: float
    kind: str
    reason: str | None
    retry_after_ms: int | None
    state_age_s: float | None
    kv_used_fraction: float | None
    waiting_requests: int
    running_requests: int
    attempt: int = 0


@runtime_checkable
class Clock(Protocol):
    """Time as a dependency, not as ambient state (spec D1).

    `VirtualClock` makes replay deterministic and fast; `WallClock` drives a
    live run. `Runner` cannot tell which it holds.
    """

    def now(self) -> float: ...

    async def sleep_until(self, t: float) -> None: ...


@runtime_checkable
class StateSource(Protocol):
    """Latest known system state. `current()` MUST NOT perform I/O.

    Per docs/design.md, state is pushed on the engine's own tick and read from
    cache per arrival — never scraped per request.
    """

    def current(self) -> SystemState: ...

    def capabilities(self) -> frozenset[str]: ...


@runtime_checkable
class EngineAdapter(Protocol):
    """Executes admitted work and emits state ticks.

    Implementations: `ReplayEngine` (deterministic, no GPU) and `VllmEngine`
    (real fleet, official telemetry). Substitutable by construction — no
    method here may assume a network or a simulation.
    """

    name: str

    async def submit(self, req: Request) -> RequestOutcome: ...

    def subscribe_ticks(self, cb: Callable[[SystemState], None]) -> None: ...

    def capabilities(self) -> frozenset[str]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class MetricsSink(Protocol):
    """Collects everything a run produces. Every value it emits is tagged with
    its provenance — engine, client, or harness (spec D5)."""

    def record_decision(self, record: DecisionRecord) -> None: ...

    def record_outcome(self, req: Request, outcome: RequestOutcome) -> None: ...

    def record_tick(self, state: SystemState) -> None: ...


@runtime_checkable
class DeferQueue(Protocol):
    """Holds DEFERred requests until they are due to be re-decided."""

    def push(self, req: Request, retry_at: float, attempt: int) -> None: ...

    def due(self, now: float) -> list[tuple[Request, int]]: ...

    def __len__(self) -> int: ...


@runtime_checkable
class BundleWriter(Protocol):
    """Persists a run as a reviewable, hash-verifiable directory."""

    def write(self, run_dir: Path, manifest: dict[str, Any]) -> Path: ...


@runtime_checkable
class TraceSource(Protocol):
    """Adapter over the frozen `TraceLoader` ABC, yielding `Request` objects.

    Kept separate from `traces.base.TraceLoader` so workloads can be generated
    without a file on disk.
    """

    name: str

    def requests(self) -> Iterable[Request]: ...

    def total(self) -> int: ...


@dataclass
class CapabilityError(Exception):
    """Raised at wiring time when a policy needs a signal the engine cannot
    provide (spec D8). Fails fast and loudly rather than degrading mid-run."""

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
    """Check once, at wiring time — never per request."""
    missing = requires - provides
    if missing:
        raise CapabilityError(policy=policy_name, engine=engine_name, missing=missing)


__all__ = [
    "ENGINE_KEY",
    "STATE_AGE_KEY",
    "BundleWriter",
    "CapabilityError",
    "Clock",
    "Decision",
    "DecisionRecord",
    "DeferQueue",
    "EngineAdapter",
    "MetricsSink",
    "RequestOutcome",
    "StateSource",
    "TraceSource",
    "check_compatibility",
]
