"""The runner loop, driven against a fake engine.

The fake is a few lines and implements the same port as vLLM, which is what the
port is for. It lets us test the loop's own behaviour — admit, reject, defer,
stale state, scrape failure — without a GPU.
"""

from __future__ import annotations

import asyncio

import pytest

from admitperf.core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from admitperf.core.ports import CapabilityError, RequestOutcome
from admitperf.core.runner import REJECT_STATUS, Runner, RunnerConfig


class FakeEngine:
    name = "fake"

    def __init__(self, *, kv: float | None = 0.1, caps: frozenset[str] | None = None) -> None:
        self.kv = kv
        self.submitted: list[str] = []
        self._caps = caps if caps is not None else frozenset({"kv_used_fraction"})
        self.fail_scrape = False

    def capabilities(self) -> frozenset[str]:
        return self._caps

    async def fetch_state(self) -> SystemState:
        if self.fail_scrape:
            raise ConnectionError("engine unreachable")
        return SystemState(
            now=0.0,
            kv_used_fraction=self.kv,
            running_requests=1,
            waiting_requests=0,
            running_agents=0,
            per_tenant_running={},
            per_tenant_admitted_recent={},
            engine_metrics={},
        )

    async def submit(self, req: Request) -> RequestOutcome:
        self.submitted.append(req.request_id)
        return RequestOutcome(request_id=req.request_id, status="completed", ttft_ms=10.0)

    async def aclose(self) -> None: ...


class Workload:
    def __init__(self, n: int = 3) -> None:
        self.n = n

    def requests(self):
        for i in range(self.n):
            yield Request(request_id=f"r{i}", tenant_id="t1", arrival_time=0.0, input_tokens=10)


class AlwaysReject(AdmissionPolicy):
    name = "always_reject"

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.reject(reason="kv_pressure")


class DeferOnce(AdmissionPolicy):
    name = "defer_once"

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def decide(self, req: Request, state: SystemState) -> Decision:
        if req.request_id not in self.seen:
            self.seen.add(req.request_id)
            return Decision.defer(retry_after_ms=1)
        return Decision.admit()


class NeedsKV(AdmissionPolicy):
    name = "needs_kv"
    requires = frozenset({"kv_used_fraction"})

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()


def _runner(policy: AdmissionPolicy, engine: FakeEngine, **kw) -> Runner:
    return Runner(
        workload=Workload(kw.pop("n", 3)),
        policy=policy,
        engine=engine,
        config=RunnerConfig(scrape_interval_s=0.01, **kw),
    )


async def test_admitted_requests_reach_the_engine() -> None:
    from admitperf.policies import NoAdmission

    engine = FakeEngine()
    result = await _runner(NoAdmission(), engine).run()

    assert result.admitted == 3
    assert result.completed == 3
    assert engine.submitted == ["r0", "r1", "r2"]


async def test_rejected_requests_never_reach_the_engine() -> None:
    engine = FakeEngine()
    result = await _runner(AlwaysReject(), engine).run()

    assert result.rejected == 3
    assert result.admitted == 0
    assert engine.submitted == []
    assert result.reject_reasons == {"kv_pressure": 3}


async def test_reject_reason_carries_an_http_status() -> None:
    result = await _runner(AlwaysReject(), FakeEngine()).run()
    statuses = {d.http_status for d in result.decisions}
    assert statuses == {REJECT_STATUS["kv_pressure"]}


async def test_decisions_record_the_state_they_were_made_on() -> None:
    """Without this, "why was this rejected" is unanswerable after the run."""
    from admitperf.policies import NoAdmission

    result = await _runner(NoAdmission(), FakeEngine(kv=0.42)).run()

    assert len(result.decisions) == 3
    assert all(d.kv_used_fraction == 0.42 for d in result.decisions)
    assert all(d.state_age_s is not None for d in result.decisions)


async def test_defer_waits_and_then_re_decides() -> None:
    engine = FakeEngine()
    result = await _runner(DeferOnce(), engine).run()

    assert result.deferred == 3
    assert result.admitted == 3  # each one admitted on the second ask
    assert sorted(engine.submitted) == ["r0", "r1", "r2"]


async def test_endless_defer_becomes_a_reject() -> None:
    class AlwaysDefer(AdmissionPolicy):
        name = "always_defer"

        def decide(self, req: Request, state: SystemState) -> Decision:
            return Decision.defer(retry_after_ms=0)

    result = await _runner(AlwaysDefer(), FakeEngine(), max_defers=3, n=1).run()

    assert result.reject_reasons == {"defer_exhausted": 1}
    assert result.admitted == 0


async def test_stale_state_sheds_rather_than_guessing() -> None:
    """An ancient snapshot is not signal. Deciding on it and recording the
    result would corrupt the comparison the run exists to produce."""
    from admitperf.policies import NoAdmission

    engine = FakeEngine()
    runner = _runner(NoAdmission(), engine, max_state_age_s=-1.0)
    result = await runner.run()

    assert result.reject_reasons == {"no_signal": 3}
    assert engine.submitted == []


async def test_policy_needing_an_absent_signal_fails_at_construction() -> None:
    with pytest.raises(CapabilityError, match="kv_used_fraction"):
        _runner(NeedsKV(), FakeEngine(caps=frozenset()))


async def test_scrape_failures_do_not_end_the_run() -> None:
    from admitperf.policies import NoAdmission

    engine = FakeEngine()
    runner = _runner(NoAdmission(), engine)

    async def flap() -> None:
        await asyncio.sleep(0.02)
        engine.fail_scrape = True

    asyncio.create_task(flap())
    result = await runner.run()

    assert result.completed == 3


async def test_run_without_a_reachable_engine_says_so() -> None:
    from admitperf.policies import NoAdmission

    engine = FakeEngine()
    engine.fail_scrape = True
    runner = _runner(NoAdmission(), engine)
    runner._wait_until_primed.__defaults__  # noqa: B018

    with pytest.raises(RuntimeError, match="is the engine up"):
        await runner._wait_until_primed(timeout_s=0.1)


async def test_decision_kinds_are_recorded_verbatim() -> None:
    from admitperf.policies import NoAdmission

    result = await _runner(NoAdmission(), FakeEngine()).run()
    assert {d.kind for d in result.decisions} == {DecisionKind.ADMIT.value}
