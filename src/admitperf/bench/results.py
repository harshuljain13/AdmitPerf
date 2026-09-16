"""Turn a run into numbers, and write them somewhere a reader can check.

Every reported value says where it came from:

  client   measured here, as the response streamed back
  harness  counted by the admission layer itself
  engine   read from the engine's own telemetry

That tagging is the difference between a number you can defend and one you
cannot. It also makes the absent ones visible: anything AdmitPerf cannot
actually measure is written down as unavailable, with the reason, rather than
estimated into existence.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from admitperf.bench.slo import Attainment, judge
from admitperf.core.api import Request
from admitperf.core.ports import RequestOutcome
from admitperf.core.runner import RunResult


def percentiles(values: list[float]) -> dict[str, float | None]:
    """p50/p95/p99 from measured samples.

    Computed here rather than read from the engine on purpose: vLLM publishes
    latency as sum/count, which is a mean over every request it has served and
    carries no distribution at all.
    """
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    ordered = sorted(values)
    n = len(ordered)

    def at(q: float) -> float:
        # Nearest-rank: the smallest value at or below which q of the samples
        # fall. Interpolating between samples would invent latencies that were
        # never observed, which is the wrong trade for a tail measurement.
        rank = math.ceil(q * n)
        return ordered[min(n - 1, max(0, rank - 1))]

    return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99)}


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(value, places)


def summarize(result: RunResult, requests: dict[str, Request] | None = None) -> dict[str, Any]:
    per_request = requests or {}

    def _measured(outcome: RequestOutcome) -> bool:
        """Warmup requests are load, not evidence. They are sent so the engine
        faces realistic pressure, but they pay for cold caches and graph
        capture, which is not the policy's doing."""
        req = per_request.get(outcome.request_id)
        return not (req and req.metadata.get("warmup"))

    outcomes = [o for o in result.outcomes if _measured(o)]
    completed = [o for o in outcomes if o.status == "completed"]
    ttfts = [o.ttft_ms for o in completed if o.ttft_ms is not None]
    tbts = [gap for o in completed for gap in o.tbt_ms]

    warm_ids = {rid for rid, r in per_request.items() if r.metadata.get("warmup")}
    # Every count comes from the same filtered set. Mixing a warmup-inclusive
    # numerator with a warmup-excluding denominator produced admit rates above
    # 100%, which is how the inconsistency announced itself.
    measured = [d for d in result.decisions if d.request_id not in warm_ids]
    admitted = sum(1 for d in measured if d.kind == "admit")
    rejected = sum(1 for d in measured if d.kind == "reject")
    deferred = sum(1 for d in measured if d.kind == "defer")
    offered = admitted + rejected
    wall = result.wall_s or 1.0

    # Judge each outcome against what its request was actually promised, so
    # TTFT and inter-token latency are assessed jointly rather than TTFT alone.
    att = Attainment(arrived=offered, admitted=admitted, rejected=rejected)
    for outcome in outcomes:
        req = per_request.get(outcome.request_id)
        if req is None:
            continue
        att.add(judge(req, outcome), outcome)

    # A refusal is only useful if it is fast, and the arrival loop falling
    # behind would inflate this — so it doubles as a coordinated-omission check.

    reject_latency = [
        d.decision_latency_ms
        for d in measured
        if d.kind == "reject" and d.decision_latency_ms is not None
    ]
    decision_lag = [
        d.decision_latency_ms for d in result.decisions if d.decision_latency_ms is not None
    ]

    return {
        "offered": offered,
        "admitted": admitted,
        "deferred": deferred,
        "rejected": rejected,
        "warmup_excluded": len(warm_ids),
        "completed": result.completed,
        "failed": result.failed,
        "reject_reasons": dict(result.reject_reasons),
        "wall_s": round(result.wall_s, 3),
        "throughput_rps": round(result.completed / wall, 3),
        # The headline. Denominator is everything that arrived, so a policy
        # cannot improve it by refusing more — only by refusing better.
        "offered_attainment": _round(att.offered),
        # How well it served what it took. Flatters shedding, so it is never
        # meaningful without the line above.
        "served_attainment": _round(att.served),
        # A rate, as published work reports it. The sustainable rate — the
        # highest load holding attainment above a target — needs a load sweep,
        # not a single run.
        "goodput_rps": round(att.met / wall, 3),
        "admit_rate": round(admitted / offered, 4) if offered else None,
        "ttft_ms": percentiles([float(v) for v in ttfts]),
        "tbt_ms": percentiles([float(v) for v in tbts]),
        "slo": {
            "judged": att.judged,
            "met": att.met,
            "missed_ttft": att.missed_ttft,
            "missed_itl": att.missed_itl,
            "failed_outright": att.failed_outright,
        },
        # What the policy saved: tokens generated for requests that missed
        # their promise anyway.
        "wasted_output_tokens": att.wasted_output_tokens,
        "useful_output_tokens": att.useful_output_tokens,
        "wasted_fraction": _round(att.wasted_fraction),
        "reject_latency_ms": percentiles([float(v) for v in reject_latency]),
        # p95 of how late every decision was against its scheduled arrival.
        # Large values mean the load generator could not keep up and the run
        # understates real latency.
        "decision_lag_p95_ms": _round(percentiles([float(v) for v in decision_lag])["p95"], 2),
        "scrapes": result.scrapes,
        "scrape_failures": result.scrape_failures,
        "scrape_error": result.scrape_error,
        # False means the policy was deciding on a stale snapshot for most of
        # the run, so these numbers describe the workload, not the policy.
        "signal_was_healthy": result.signal_was_healthy,
        "sources": {
            "ttft_ms": "client",
            "offered_attainment": "client+harness",
            "served_attainment": "client+harness",
            "goodput_rps": "client+harness",
            "tbt_ms": "client",
            "admit_rate": "harness",
            "reject_reasons": "harness",
            "throughput_rps": "client",
        },
        "unavailable": {
            "preemption_loss_bytes": (
                "no engine reports KV bytes discarded to preemption; "
                "vllm:num_preemptions_total is a count, not a volume"
            ),
            "gpu_utilization": "needs DCGM alongside the engine; not collected",
        },
    }


def write_bundle(
    out_dir: Path,
    *,
    result: RunResult,
    manifest: dict[str, Any],
) -> Path:
    """Write the run as a directory.

    A directory rather than an archive, because the point is that someone can
    look: diff two runs, grep the decisions, see why a request was refused.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(result, result.requests)

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    with (out_dir / "decisions.jsonl").open("w") as fh:
        for record in result.decisions:
            fh.write(json.dumps(asdict(record)) + "\n")

    with (out_dir / "outcomes.jsonl").open("w") as fh:
        for outcome in result.outcomes:
            row = asdict(outcome)
            row["tbt_ms"] = list(row["tbt_ms"])
            fh.write(json.dumps(row) + "\n")

    return out_dir


__all__ = ["percentiles", "summarize", "write_bundle"]
