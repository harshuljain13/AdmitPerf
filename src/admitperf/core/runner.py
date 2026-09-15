"""The loop: scrape state in the background, decide on each arrival, forward or refuse.

Two loops at different speeds. A background task scrapes the engine every
`scrape_interval_s` and updates the cache. The arrival loop releases requests
on the workload's schedule, asks the policy about each one, and either sends it
to the engine or refuses it.

They are separate because the decision path must not scrape. Putting an HTTP
round trip in front of every arrival would add latency to the decision and load
the engine with exactly the traffic admission control exists to shed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from admitperf.core.api import AdmissionPolicy, Decision, DecisionKind, Request, SystemState
from admitperf.core.ports import (
    DecisionRecord,
    EngineAdapter,
    RequestOutcome,
    check_compatibility,
)
from admitperf.core.registry import requirements_of
from admitperf.core.state import StateCache, state_age

#: Reject reason → HTTP status, per docs/design.md. The distinction is for the
#: caller: 429 means try again shortly, 503 means this fleet cannot serve you
#: right now, 529 means retrying will not help.
REJECT_STATUS: dict[str, int] = {
    "queue_depth": 429,
    "kv_pressure": 429,
    "no_signal": 503,
    "no_headroom": 503,
    "deadline_unmeetable": 529,
    "defer_exhausted": 529,
}
DEFAULT_REJECT_STATUS = 429


@dataclass
class RunnerConfig:
    scrape_interval_s: float = 0.1
    #: Beyond this, the snapshot is too old to decide on. module7 used the same
    #: idea in production and shed with `no_signal`.
    max_state_age_s: float = 5.0
    #: A policy that defers under pressure, on a fleet that stays under
    #: pressure, would hold a request forever. Past this it becomes a reject,
    #: so the outcome appears in the histogram instead of vanishing.
    max_defers: int = 100


@dataclass
class RunResult:
    admitted: int = 0
    deferred: int = 0
    rejected: int = 0
    completed: int = 0
    failed: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)
    decisions: list[DecisionRecord] = field(default_factory=list)
    outcomes: list[RequestOutcome] = field(default_factory=list)
    scrapes: int = 0
    scrape_failures: int = 0
    #: First scrape error seen, kept so a run that measured nothing can say why.
    scrape_error: str | None = None

    @property
    def signal_was_healthy(self) -> bool:
        """Did the policy actually get to see the fleet?

        A run where most scrapes failed produces numbers that look ordinary and
        mean nothing: the policy decides on a stale snapshot taken while idle,
        so it admits everything and scores identically to the baseline. That
        happened on the first real deployment, and nothing in the output said
        so.
        """
        total = self.scrapes + self.scrape_failures
        return total > 0 and self.scrapes / total >= 0.5

    wall_s: float = 0.0


class Runner:
    def __init__(
        self,
        *,
        workload: object,
        policy: AdmissionPolicy,
        engine: EngineAdapter,
        config: RunnerConfig | None = None,
    ) -> None:
        self.workload = workload
        self.policy = policy
        self.engine = engine
        self.config = config or RunnerConfig()
        self.states = StateCache()
        self.result = RunResult()

        # Once, here. A policy that needs a signal this engine cannot report
        # would otherwise read the missing value as zero and quietly turn into
        # an admit-everything baseline.
        check_compatibility(
            policy_name=type(policy).__name__,
            requires=requirements_of(policy),
            engine_name=engine.name,
            provides=engine.capabilities(),
        )

    async def run(self) -> RunResult:
        started = time.monotonic()
        stop = asyncio.Event()
        scraper = asyncio.create_task(self._scrape_loop(stop))

        # Wait for the first successful scrape, so the first decision is made
        # on real signal rather than on an empty cache.
        await self._wait_until_primed()

        inflight: list[asyncio.Task[RequestOutcome]] = []
        try:
            for req in self.workload.requests():  # type: ignore[attr-defined]
                await self._wait_for_arrival(started, req)
                task = self._handle(req, inflight)
                if task is not None:
                    inflight.append(task)

            for outcome in await asyncio.gather(*inflight):
                self._tally(outcome)
        finally:
            stop.set()
            await scraper

        self.result.scrapes = self.states.scrapes
        self.result.scrape_failures = self.states.scrape_failures
        self.result.wall_s = time.monotonic() - started
        return self.result

    # --- the two loops ----------------------------------------------------

    async def _scrape_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                self.states.update(await self.engine.fetch_state())
            except Exception as exc:  # noqa: BLE001 - a blip must not end the run
                self.states.record_failure()
                if self.result.scrape_error is None:
                    self.result.scrape_error = f"{type(exc).__name__}: {exc}"
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.config.scrape_interval_s)

    async def _wait_until_primed(self, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while not self.states.primed:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"no successful scrape from {self.engine.name} within {timeout_s:.0f}s; "
                    "is the engine up and serving /metrics?"
                )
            await asyncio.sleep(0.05)

    async def _wait_for_arrival(self, started: float, req: Request) -> None:
        """Hold until this request is due, so offered load matches the trace."""
        due = started + req.arrival_time
        delay = due - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    # --- one request ------------------------------------------------------

    def _handle(
        self, req: Request, inflight: list[asyncio.Task[RequestOutcome]], attempt: int = 0
    ) -> asyncio.Task[RequestOutcome] | None:
        state = self.states.current()
        age = state_age(state)

        if age > self.config.max_state_age_s:
            # No usable signal. Shedding is the honest answer: deciding on a
            # snapshot this old is guessing, and a guess recorded as a decision
            # would corrupt the comparison.
            decision = Decision.reject(reason="no_signal")
        else:
            decision = self.policy.decide(req, state)

        if decision.kind is DecisionKind.DEFER and attempt >= self.config.max_defers:
            decision = Decision.reject(reason="defer_exhausted")

        self._record(req, decision, state)

        if decision.kind is DecisionKind.ADMIT:
            self.result.admitted += 1
            self.policy.on_admit(req, state)
            return asyncio.create_task(self.engine.submit(req))

        if decision.kind is DecisionKind.DEFER:
            self.result.deferred += 1
            inflight.append(
                asyncio.create_task(self._retry_later(req, decision, inflight, attempt))
            )
            return None

        self.result.rejected += 1
        reason = decision.reason or "unspecified"
        self.result.reject_reasons[reason] = self.result.reject_reasons.get(reason, 0) + 1
        return None

    async def _retry_later(
        self,
        req: Request,
        decision: Decision,
        inflight: list[asyncio.Task[RequestOutcome]],
        attempt: int,
    ) -> RequestOutcome:
        """DEFER against a live engine: wait, then ask again.

        No queue needed — the wait is just a sleep, and the policy sees fresher
        state when it is asked a second time.
        """
        await asyncio.sleep((decision.retry_after_ms or 0) / 1000.0)
        task = self._handle(req, inflight, attempt + 1)
        if task is None:
            return RequestOutcome(request_id=req.request_id, status="failed", error="not_admitted")
        return await task

    def _record(self, req: Request, decision: Decision, state: SystemState) -> None:
        status = None
        if decision.kind is DecisionKind.REJECT:
            status = REJECT_STATUS.get(decision.reason or "", DEFAULT_REJECT_STATUS)
        self.result.decisions.append(
            DecisionRecord(
                request_id=req.request_id,
                tenant_id=req.tenant_id,
                decided_at=time.monotonic(),
                kind=decision.kind.value,
                reason=decision.reason,
                http_status=status,
                state_age_s=state_age(state),
                kv_used_fraction=state.kv_used_fraction,
                waiting_requests=state.waiting_requests,
                running_requests=state.running_requests,
            )
        )

    def _tally(self, outcome: RequestOutcome) -> None:
        self.result.outcomes.append(outcome)
        if outcome.status == "completed":
            self.result.completed += 1
        else:
            self.result.failed += 1


__all__ = ["DEFAULT_REJECT_STATUS", "REJECT_STATUS", "RunResult", "Runner", "RunnerConfig"]
