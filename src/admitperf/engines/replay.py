"""ReplayEngine — a simulated fleet, for determinism and CI.

Read this first: **no number from this engine is a performance claim.** It is
not calibrated against hardware and it never will be. Its job is to make the
decision path runnable without a GPU, so that policies can be unit-tested and a
run can be proven byte-for-byte reproducible. Real numbers come from
VllmEngine against the engine's own telemetry (spec D4).

The model is deliberately coarse (spec D12). Two phases:

    prefill: input_tokens / prefill_tokens_per_s
    decode:  output_tokens * seconds_per_output_token

and one shared resource, a KV budget in tokens. A request occupies its full
context for as long as it runs; when the budget is exhausted, the newest
running request is preempted and returns its tokens.

What this deliberately does NOT model: per-token scheduling, continuous
batching interference, prefix-cache reuse, chunked prefill, tensor-parallel
effects. Because inter-token timing is not modelled, this engine reports **no
TBT at all** rather than a plausible-looking number that would be fiction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from admitperf.core.api import Request, SystemState
from admitperf.core.ports import Clock, RequestOutcome

#: Signals this engine can populate. Used for the capability check at wiring
#: time, so a policy needing something absent fails loudly up front.
REPLAY_CAPABILITIES = frozenset(
    {
        "kv_used_fraction",
        "running_requests",
        "waiting_requests",
        "per_tenant_running",
    }
)


@dataclass
class _InFlight:
    req: Request
    kv_tokens: int
    started_at: float
    finishes_at: float
    ttft_at: float
    output_tokens: int
    seq: int = 0


@dataclass
class ReplayConfig:
    """Service rates for the simulated fleet.

    Defaults are in the neighbourhood of a 7B model on one mid-range GPU. They
    are a plausible starting point for exercising the decision path, not a
    measurement of anything.
    """

    prefill_tokens_per_s: float = 8000.0
    seconds_per_output_token: float = 0.020
    kv_budget_tokens: int = 100_000
    max_concurrent: int = 32
    tick_interval_s: float = 0.05
    preemption_enabled: bool = True
    metrics: dict[str, float] = field(default_factory=dict)


class ReplayEngine:
    """Deterministic simulated engine.

    Determinism comes from the injected clock plus insertion-ordered
    bookkeeping: there is no wall-clock read and no randomness anywhere in
    here, so the same trace produces the same outcomes every time.
    """

    name = "replay"

    def __init__(self, clock: Clock, config: ReplayConfig | None = None) -> None:
        self._clock = clock
        self.config = config or ReplayConfig()
        self._running: list[_InFlight] = []
        self._waiting = 0
        self._subscribers: list[Callable[[SystemState], None]] = []
        self._preemptions = 0
        self._completions = 0
        self._seq = 0
        self._last_tick_at: float | None = None

    # --- port surface ----------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        return REPLAY_CAPABILITIES

    def subscribe_ticks(self, cb: Callable[[SystemState], None]) -> None:
        self._subscribers.append(cb)

    async def submit(self, req: Request) -> RequestOutcome:
        """Run one request to completion in simulated time."""
        now = self._clock.now()
        output_tokens = req.expected_output_tokens or 128
        kv_tokens = req.input_tokens + output_tokens

        self._make_room(kv_tokens, now)

        prefill_s = req.input_tokens / self.config.prefill_tokens_per_s
        decode_s = output_tokens * self.config.seconds_per_output_token

        self._seq += 1
        entry = _InFlight(
            req=req,
            kv_tokens=kv_tokens,
            started_at=now,
            ttft_at=now + prefill_s,
            finishes_at=now + prefill_s + decode_s,
            output_tokens=output_tokens,
            seq=self._seq,
        )
        self._running.append(entry)
        self._emit_tick()

        await self._clock.sleep_until(entry.finishes_at)

        if entry not in self._running:
            # Preempted while we were sleeping; its KV was already reclaimed.
            self._emit_tick()
            return RequestOutcome(
                request_id=req.request_id,
                status="preempted",
                error="preempted_under_kv_pressure",
            )

        self._running.remove(entry)
        self._completions += 1
        self._emit_tick()

        ttft_ms = (entry.ttft_at - entry.started_at) * 1000.0
        total_ms = (entry.finishes_at - entry.started_at) * 1000.0
        return RequestOutcome(
            request_id=req.request_id,
            status="completed",
            ttft_ms=ttft_ms,
            # No TBT: this engine does not model inter-token timing, and a
            # synthesised value would be indistinguishable from a measured one.
            tbt_ms=(),
            total_ms=total_ms,
            output_tokens=entry.output_tokens,
            met_deadline=self._met_deadline(req, ttft_ms),
        )

    async def aclose(self) -> None:
        self._running.clear()

    # --- simulation internals --------------------------------------------

    def _make_room(self, needed: int, now: float) -> None:
        """Preempt newest-first until the incoming request fits.

        Newest-first is a choice, not a law: it protects requests that have
        already invested compute, which is what vLLM's own recompute-preemption
        effectively does. It is recorded here so the bias is visible rather
        than accidental.
        """
        if not self.config.preemption_enabled:
            return
        budget = self.config.kv_budget_tokens
        while self._running and self._kv_used() + needed > budget:
            victim = max(self._running, key=lambda e: e.seq)
            self._running.remove(victim)
            self._preemptions += 1

    def _kv_used(self) -> int:
        return sum(e.kv_tokens for e in self._running)

    def _met_deadline(self, req: Request, ttft_ms: float) -> bool | None:
        if req.deadline_ttft_ms is None:
            return None
        return ttft_ms <= req.deadline_ttft_ms

    def set_waiting(self, n: int) -> None:
        """Let the runner report its own queue depth into the state stream."""
        self._waiting = n

    def _emit_tick(self) -> None:
        state = self.snapshot()
        for cb in self._subscribers:
            cb(state)

    def maybe_tick(self) -> None:
        """Emit a tick if the interval has elapsed.

        Real engines publish metrics on a scrape interval, not on every state
        change. Mirroring that here means policies face the same staleness they
        would in production instead of a perfectly fresh view.
        """
        now = self._clock.now()
        if self._last_tick_at is None or now - self._last_tick_at >= self.config.tick_interval_s:
            self._last_tick_at = now
            self._emit_tick()

    def snapshot(self) -> SystemState:
        now = self._clock.now()
        used = self._kv_used()
        per_tenant: dict[str, int] = {}
        for entry in self._running:
            per_tenant[entry.req.tenant_id] = per_tenant.get(entry.req.tenant_id, 0) + 1

        metrics = dict(self.config.metrics)
        metrics["replay:preemptions_total"] = float(self._preemptions)
        metrics["replay:completions_total"] = float(self._completions)

        return SystemState(
            now=now,
            kv_used_fraction=min(1.0, used / self.config.kv_budget_tokens),
            running_requests=len(self._running),
            waiting_requests=self._waiting,
            running_agents=0,
            per_tenant_running=per_tenant,
            per_tenant_admitted_recent={},
            engine_metrics=metrics,
        )

    @property
    def preemptions(self) -> int:
        return self._preemptions


__all__ = ["REPLAY_CAPABILITIES", "ReplayConfig", "ReplayEngine"]
