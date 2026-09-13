"""Task 2.1 — the simulated engine.

Two properties under test: it is deterministic, and it is honest about what it
does not model (no TBT, because inter-token timing is not simulated).

Note the shape of these tests: `submit` is awaited as a task and resolved by
`settle`, because an outcome is only known once simulated time has passed the
request's completion. Deciding at submit would mean reporting success before
the request was safe from preemption.
"""

from __future__ import annotations

import asyncio

from admitperf.core.api import Request, SystemState
from admitperf.core.clock import VirtualClock
from admitperf.core.ports import EngineAdapter, RequestOutcome
from admitperf.engines.replay import ReplayConfig, ReplayEngine


def _req(rid: str, *, inp: int = 100, out: int = 50, ttft_deadline: float | None = None) -> Request:
    return Request(
        request_id=rid,
        tenant_id="t1",
        arrival_time=0.0,
        input_tokens=inp,
        expected_output_tokens=out,
        deadline_ttft_ms=ttft_deadline,
    )


async def _run_one(engine: ReplayEngine, clock: VirtualClock, req: Request) -> RequestOutcome:
    """Submit, advance simulated time to the end, and collect the outcome."""
    task = asyncio.create_task(engine.submit(req))
    await asyncio.sleep(0)  # let submit register the interval
    await clock.sleep_until(engine.drain())
    engine.settle(clock.now())
    return await task


def test_satisfies_the_engine_port() -> None:
    assert isinstance(ReplayEngine(VirtualClock()), EngineAdapter)


async def test_timings_follow_the_two_phase_model() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(
        clock,
        ReplayConfig(prefill_tokens_per_s=1000.0, seconds_per_output_token=0.01),
    )

    outcome = await _run_one(engine, clock, _req("a", inp=1000, out=100))

    assert outcome.status == "completed"
    assert outcome.ttft_ms == 1000.0  # 1000 tokens / 1000 tok/s = 1.0s
    assert outcome.total_ms == 2000.0  # + 100 tokens * 10ms = 1.0s


async def test_submit_does_not_advance_the_clock() -> None:
    """The guard against a long request dragging time past later arrivals."""
    clock = VirtualClock()
    engine = ReplayEngine(clock)

    task = asyncio.create_task(engine.submit(_req("slow", inp=100_000, out=10_000)))
    await asyncio.sleep(0)

    assert clock.now() == 0.0, "submit moved the clock; later arrivals would collapse"

    await clock.sleep_until(engine.drain())
    engine.settle(clock.now())
    await task


async def test_reports_no_tbt_because_it_does_not_model_one() -> None:
    """Honesty over plausibility: a synthesised inter-token latency would be
    indistinguishable from a measured one in the results bundle."""
    clock = VirtualClock()
    engine = ReplayEngine(clock)
    outcome = await _run_one(engine, clock, _req("a"))
    assert outcome.tbt_ms == ()


async def test_is_deterministic_across_runs() -> None:
    async def run() -> list[tuple[str, float | None]]:
        clock = VirtualClock()
        engine = ReplayEngine(clock)
        out = []
        for i in range(50):
            o = await _run_one(engine, clock, _req(f"r{i}", inp=100 + i, out=20 + i))
            out.append((o.status, o.total_ms))
        return out

    assert await run() == await run()


async def test_kv_pressure_rises_as_requests_run() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(kv_budget_tokens=10_000))
    seen: list[SystemState] = []
    engine.subscribe_ticks(seen.append)

    await _run_one(engine, clock, _req("a", inp=1000, out=1000))

    assert seen, "engine should emit ticks"
    peak = max(s.kv_used_fraction or 0.0 for s in seen)
    assert 0.0 < peak <= 1.0


async def test_preemption_reclaims_kv_and_reports_it_as_preempted() -> None:
    """Oversubscribe the budget: the engine must shed rather than quietly
    exceed it, and the shed request must be reported as preempted rather than
    as a success."""
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(kv_budget_tokens=1_000))

    tasks = [asyncio.create_task(engine.submit(_req(f"r{i}", inp=300, out=300))) for i in range(3)]
    await asyncio.sleep(0)

    await clock.sleep_until(engine.drain())
    engine.settle(clock.now())
    outcomes = await asyncio.gather(*tasks)

    assert engine.preemptions >= 1
    assert any(o.status == "preempted" for o in outcomes)
    assert any(o.status == "completed" for o in outcomes)


async def test_kv_never_exceeds_the_budget() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(kv_budget_tokens=2_000))
    seen: list[SystemState] = []
    engine.subscribe_ticks(seen.append)

    tasks = [asyncio.create_task(engine.submit(_req(f"r{i}", inp=400, out=400))) for i in range(6)]
    await asyncio.sleep(0)
    await clock.sleep_until(engine.drain())
    engine.settle(clock.now())
    await asyncio.gather(*tasks)

    assert all((s.kv_used_fraction or 0.0) <= 1.0 for s in seen)


async def test_deadline_is_evaluated_against_ttft() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(prefill_tokens_per_s=1000.0))

    met = await _run_one(engine, clock, _req("fast", inp=100, ttft_deadline=500.0))
    missed = await _run_one(engine, clock, _req("slow", inp=10_000, ttft_deadline=500.0))

    assert met.met_deadline is True  # 100ms <= 500ms
    assert missed.met_deadline is False  # 10s > 500ms


async def test_no_deadline_means_no_verdict() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(clock)
    outcome = await _run_one(engine, clock, _req("a", ttft_deadline=None))
    assert outcome.met_deadline is None


def test_declares_what_it_can_provide() -> None:
    caps = ReplayEngine(VirtualClock()).capabilities()
    assert "kv_used_fraction" in caps
    assert "running_requests" in caps
