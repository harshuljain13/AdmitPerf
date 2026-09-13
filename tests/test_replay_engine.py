"""Task 2.1 — the simulated engine.

Two things under test: it is deterministic, and it is honest about what it does
not model (no TBT, because inter-token timing is not simulated).
"""

from __future__ import annotations

from admitperf.core.api import Request, SystemState
from admitperf.core.clock import VirtualClock
from admitperf.core.ports import EngineAdapter
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


def test_satisfies_the_engine_port() -> None:
    assert isinstance(ReplayEngine(VirtualClock()), EngineAdapter)


async def test_completes_a_request_in_simulated_time() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(
        clock,
        ReplayConfig(prefill_tokens_per_s=1000.0, seconds_per_output_token=0.01),
    )

    outcome = await engine.submit(_req("a", inp=1000, out=100))

    assert outcome.status == "completed"
    assert outcome.ttft_ms == 1000.0  # 1000 tokens / 1000 tok/s = 1.0s
    assert outcome.total_ms == 2000.0  # + 100 tokens * 10ms = 1.0s
    assert clock.now() == 2.0


async def test_reports_no_tbt_because_it_does_not_model_one() -> None:
    """Honesty over plausibility: a synthesised inter-token latency would be
    indistinguishable from a measured one in the results bundle."""
    engine = ReplayEngine(VirtualClock())
    outcome = await engine.submit(_req("a"))
    assert outcome.tbt_ms == ()


async def test_is_deterministic_across_runs() -> None:
    async def run() -> list[tuple[str, float | None]]:
        engine = ReplayEngine(VirtualClock())
        out = []
        for i in range(50):
            o = await engine.submit(_req(f"r{i}", inp=100 + i, out=20 + i))
            out.append((o.status, o.total_ms))
        return out

    assert await run() == await run()


async def test_kv_pressure_rises_as_requests_run() -> None:
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(kv_budget_tokens=10_000))
    seen: list[SystemState] = []
    engine.subscribe_ticks(seen.append)

    await engine.submit(_req("a", inp=1000, out=1000))

    assert seen, "engine should emit ticks"
    peak = max(s.kv_used_fraction or 0.0 for s in seen)
    assert 0.0 < peak <= 1.0


async def test_preemption_reclaims_kv_when_the_budget_is_exceeded() -> None:
    """Oversubscribe the budget and confirm the engine sheds rather than
    silently exceeding it."""
    clock = VirtualClock()
    engine = ReplayEngine(clock, ReplayConfig(kv_budget_tokens=1_000))

    # Each request wants 600 tokens; two fit, the third forces a preemption.
    import asyncio

    tasks = [asyncio.create_task(engine.submit(_req(f"r{i}", inp=300, out=300))) for i in range(3)]
    outcomes = await asyncio.gather(*tasks)

    assert engine.preemptions >= 1
    assert any(o.status == "preempted" for o in outcomes)
    assert any(o.status == "completed" for o in outcomes)


async def test_deadline_is_evaluated_against_ttft() -> None:
    engine = ReplayEngine(
        VirtualClock(),
        ReplayConfig(prefill_tokens_per_s=1000.0),
    )

    met = await engine.submit(_req("fast", inp=100, ttft_deadline=500.0))
    missed = await engine.submit(_req("slow", inp=10_000, ttft_deadline=500.0))

    assert met.met_deadline is True  # 100ms <= 500ms
    assert missed.met_deadline is False  # 10s > 500ms


async def test_no_deadline_means_no_verdict() -> None:
    engine = ReplayEngine(VirtualClock())
    outcome = await engine.submit(_req("a", ttft_deadline=None))
    assert outcome.met_deadline is None


def test_declares_what_it_can_provide() -> None:
    caps = ReplayEngine(VirtualClock()).capabilities()
    assert "kv_used_fraction" in caps
    assert "running_requests" in caps
