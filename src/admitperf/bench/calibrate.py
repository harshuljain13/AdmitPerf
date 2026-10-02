"""Measure the engine's latency with nothing else running.

Relative SLOs are multiples of this. Fixed milliseconds cannot be compared
across regimes — 500ms is generous for a 0.5B model on one GPU and impossible
for a 70B model, so a single threshold would be measuring the model rather than
the policy. Expressing deadlines as "3x unloaded" means the same thing in both
places.

The measurement has to be taken at a trickle. Send requests fast enough to
queue and the baseline includes queueing, which is the very thing the SLO is
supposed to detect.
"""

from __future__ import annotations

import asyncio

from admitperf.bench.slo import percentile
from admitperf.bench.workloads.poisson import Baseline
from admitperf.core.api import Request
from admitperf.core.ports import EngineAdapter


async def calibrate(
    engine: EngineAdapter,
    *,
    samples: int = 12,
    input_tokens: int = 256,
    output_tokens: int = 64,
    gap_s: float = 1.0,
) -> Baseline:
    """Send `samples` requests one at a time and record unloaded latency.

    Strictly sequential, with a gap between each. Concurrency here would
    contaminate the baseline with exactly the contention it exists to measure
    the absence of.
    """
    ttfts: list[float] = []
    itls: list[float] = []

    for i in range(samples):
        outcome = await engine.submit(
            Request(
                request_id=f"calibrate-{i:03d}",
                tenant_id="calibrate",
                arrival_time=0.0,
                input_tokens=input_tokens,
                expected_output_tokens=output_tokens,
            )
        )
        if outcome.status == "completed" and outcome.ttft_ms is not None:
            ttfts.append(outcome.ttft_ms)
            itls.extend(outcome.tbt_ms)
        await asyncio.sleep(gap_s)

    if not ttfts:
        raise RuntimeError("calibration produced no successful requests; the engine is not serving")

    ttft_p50 = percentile(ttfts, 0.50)
    itl_p50 = percentile(itls, 0.50)
    assert ttft_p50 is not None

    return Baseline(
        ttft_p50_ms=ttft_p50,
        # A single-token response yields no inter-token gap. Falling back to
        # the TTFT median keeps a relative TBT deadline finite rather than
        # silently disabling it.
        itl_p50_ms=itl_p50 if itl_p50 is not None else ttft_p50,
        samples=len(ttfts),
    )


__all__ = ["calibrate"]
