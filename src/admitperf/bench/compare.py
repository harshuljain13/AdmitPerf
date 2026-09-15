"""Compare runs, and say when a difference is not one.

The point of reporting spread alongside the median is to stop the reader — who
is usually you, later — from believing a 3% gap that is smaller than the
run-to-run noise. A benchmark that prints one confident number per policy
invites exactly that mistake.

With a single repeat there is no spread to report, and this says so rather than
implying precision it does not have.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PolicyStats:
    label: str
    runs: int = 0
    admitted: list[float] = field(default_factory=list)
    offered: list[float] = field(default_factory=list)
    ttft_p95: list[float] = field(default_factory=list)
    goodput: list[float] = field(default_factory=list)
    rejects: dict[str, int] = field(default_factory=dict)

    unhealthy: int = 0

    def add(self, summary: dict[str, Any]) -> None:
        self.runs += 1
        if summary.get("signal_was_healthy") is False:
            self.unhealthy += 1
        self.offered.append(float(summary.get("offered") or 0))
        self.admitted.append(float(summary.get("admitted") or 0))
        if (p95 := (summary.get("ttft_ms") or {}).get("p95")) is not None:
            self.ttft_p95.append(float(p95))
        if (g := summary.get("goodput_under_admission")) is not None:
            self.goodput.append(float(g))
        for reason, count in (summary.get("reject_reasons") or {}).items():
            self.rejects[reason] = self.rejects.get(reason, 0) + int(count)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _spread(values: list[float]) -> str:
    """Half the min-max range, as ±. Not a confidence interval: with two or
    three repeats it is a range, and calling it anything else would overstate
    what a handful of runs can support."""
    if len(values) < 2:
        return ""
    return f" ±{(max(values) - min(values)) / 2:.0f}"


def load_runs(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Every (policy label, summary) under a directory tree."""
    runs: list[tuple[str, dict[str, Any]]] = []
    for summary_path in sorted(root.rglob("summary.json")):
        manifest_path = summary_path.parent / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            summary = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            continue
        label = manifest.get("policy_label") or manifest.get("policy") or "unknown"
        runs.append((label, summary))
    return runs


def compare_dir(root: Path) -> str | None:
    runs = load_runs(root)
    if not runs:
        return None

    stats: dict[str, PolicyStats] = {}
    for label, summary in runs:
        stats.setdefault(label, PolicyStats(label)).add(summary)

    width = max(len(s.label) for s in stats.values())
    lines = [
        f"{len(runs)} run(s) across {len(stats)} "
        f"{'policy' if len(stats) == 1 else 'policies'} under {root}",
        "",
        f"{'policy':<{width}}  {'runs':>4}  {'admit%':>8}  {'TTFT p95':>14}  {'goodput':>9}",
        f"{'-' * width}  {'-' * 4}  {'-' * 8}  {'-' * 14}  {'-' * 9}",
    ]

    for s in sorted(stats.values(), key=lambda x: x.label):
        rates = [a / o for a, o in zip(s.admitted, s.offered, strict=True) if o]
        admit = _median(rates)
        ttft = _median(s.ttft_p95)
        good = _median(s.goodput)
        lines.append(
            f"{s.label:<{width}}  {s.runs:>4}  "
            f"{'-' if admit is None else f'{admit * 100:7.1f}%'}  "
            f"{('-' if ttft is None else f'{ttft:.0f}ms') + _spread(s.ttft_p95):>14}  "
            f"{'-' if good is None else f'{good:9.4f}'}"
        )

    lines.append("")
    lines.append("medians across repeats; ± is half the observed range, not a CI.")

    degraded = [s.label for s in stats.values() if s.unhealthy]
    if degraded:
        lines.append(
            f"WARNING: {', '.join(sorted(degraded))} had runs where most scrapes "
            "failed. The policy saw stale state and those numbers do not "
            "describe it."
        )

    single = [s.label for s in stats.values() if s.runs == 1]
    if single:
        lines.append(
            f"only one run for {', '.join(sorted(single))} — no spread to report. "
            "Use --repeats 3 before trusting a difference."
        )

    reasons = {r for s in stats.values() for r in s.rejects}
    if reasons:
        lines.append("")
        lines.append("reject reasons:")
        for s in sorted(stats.values(), key=lambda x: x.label):
            if s.rejects:
                detail = ", ".join(f"{k}={v}" for k, v in sorted(s.rejects.items()))
                lines.append(f"  {s.label:<{width}}  {detail}")

    return "\n".join(lines)


__all__ = ["PolicyStats", "compare_dir", "load_runs"]
