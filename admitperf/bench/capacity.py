"""Find what the deployment can actually serve, before comparing policies.

An admission policy is only interesting relative to capacity: below it there is
nothing to refuse, far above it every policy is reduced to choosing what to
throw away. So "which policy wins" is really "which policy wins at this
multiple of capacity", and picking round numbers for the load axis — 4/s, 8/s,
12/s — hides that behind arbitrary constants that do not transfer to other
hardware.

This measures the serveable rate directly: run the baseline at a ramp of
arrival rates and find where it stops meeting its SLOs. Everything downstream
is then expressed as a multiple of that number, which is a statement about the
deployment rather than about the experimenter's guess.

Deliberately uses `no_admission`: capacity is a property of the engine, and
measuring it through a policy that refuses traffic would measure the policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from admitperf.bench.experiment import run_one
from admitperf.bench.workloads.poisson import Baseline
from admitperf.core.config import ExperimentConfig, PolicySpec, WorkloadConfig


@dataclass
class Rung:
    """One rate on the ramp."""

    rate: float
    offered: int
    admitted: int
    attainment: float | None
    ttft_p95_ms: float | None
    failed: int = 0


@dataclass
class Capacity:
    """What the deployment serves, and where it stops."""

    rungs: list[Rung] = field(default_factory=list)
    #: Highest rate whose attainment still met `target`.
    serveable_rate: float | None = None
    #: First rate that fell below `target`. Capacity is between the two.
    first_failing_rate: float | None = None
    target: float = 0.9

    def suggested_loads(self, multiples: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0)) -> list[float]:
        """The load axis for a policy comparison, as multiples of capacity."""
        if self.serveable_rate is None:
            return []
        return [round(self.serveable_rate * m, 2) for m in multiples]


async def measure(
    cfg: ExperimentConfig,
    *,
    engine_url: str,
    served_model: str,
    rates: list[float],
    target: float = 0.9,
    duration_s: float = 30.0,
    warmup_s: float = 10.0,
    baseline: Baseline | None = None,
    echo: Any = print,
) -> Capacity:
    """Walk the ramp until the baseline stops meeting its SLOs.

    Stops early on the first failing rung: everything above it fails too, and
    the point of this is to spend a few minutes rather than an hour.
    """
    cap = Capacity(target=target)
    spec = PolicySpec("no_admission")

    for rate in sorted(rates):
        wl = WorkloadConfig(
            kind=cfg.workload.kind,
            n=cfg.workload.n,
            rate=rate,
            seed=cfg.workload.seed,
            duration_s=duration_s,
            warmup_s=warmup_s,
            slo_mode=cfg.workload.slo_mode,
        )
        summary, _ = await run_one(
            cfg,
            spec,
            engine_url=engine_url,
            served_model=served_model,
            seed=cfg.workload.seed,
            baseline=baseline,
            workload_cfg=wl,
        )
        rung = Rung(
            rate=rate,
            offered=int(summary.get("offered") or 0),
            admitted=int(summary.get("admitted") or 0),
            attainment=summary.get("offered_attainment"),
            ttft_p95_ms=(summary.get("ttft_ms") or {}).get("p95"),
            failed=int(summary.get("failed") or 0),
        )
        cap.rungs.append(rung)
        att = "-" if rung.attainment is None else f"{rung.attainment:.3f}"
        ttft = "-" if rung.ttft_p95_ms is None else f"{rung.ttft_p95_ms:.0f}ms"
        echo(f"  {rate:>6.1f}/s  attainment {att}  TTFT p95 {ttft}  failed {rung.failed}")

        if rung.attainment is not None and rung.attainment >= target:
            cap.serveable_rate = rate
        elif rung.attainment is not None:
            cap.first_failing_rate = rate
            break

    return cap


def render_text(cap: Capacity) -> str:
    lines = [
        f"{'rate':>8}  {'offered':>8}  {'attainment':>10}  {'TTFT p95':>9}  {'failed':>6}",
        f"{'-' * 8}  {'-' * 8}  {'-' * 10}  {'-' * 9}  {'-' * 6}",
    ]
    for r in cap.rungs:
        att = "-" if r.attainment is None else f"{r.attainment:.3f}"
        ttft = "-" if r.ttft_p95_ms is None else f"{r.ttft_p95_ms:.0f}ms"
        lines.append(f"{r.rate:>7.1f}/s  {r.offered:>8}  {att:>10}  {ttft:>9}  {r.failed:>6}")

    lines.append("")
    if cap.serveable_rate is None:
        lines.append(
            f"No rate met {cap.target:.0%} attainment, including the lowest tried. "
            "Either the deployment is smaller than the traffic's smallest step, "
            "or the SLOs are tighter than the engine can ever meet — calibrate "
            "and use relative deadlines before reading anything into a policy."
        )
        return "\n".join(lines)

    lines.append(
        f"serveable: {cap.serveable_rate:g}/s at {cap.target:.0%} attainment"
        + (
            f"; {cap.first_failing_rate:g}/s already fails, so capacity is between them"
            if cap.first_failing_rate
            else " (the ramp never failed — capacity is at or above the top rung)"
        )
    )
    loads = cap.suggested_loads()
    if loads:
        lines.append("")
        lines.append("a load axis grounded in that, for a policy comparison:")
        lines.append("")
        lines.append("loads:")
        for rate, mult in zip(loads, (0.5, 1.0, 1.5, 2.0), strict=False):
            lines.append(f"  - rate: {rate:g}     # {mult:g}x capacity")
    return "\n".join(lines)


__all__ = ["Capacity", "Rung", "measure", "render_text"]
