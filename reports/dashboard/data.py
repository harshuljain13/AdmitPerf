"""Load run bundles into frames the app can chart.

Reads the same directories `bench run` writes, so nothing here needs the
harness installed or a GPU present — you can point it at results copied off a
machine that has since been torn down.

One rule runs through this module: **a deployment is the unit of comparison.**
Policies are comparable only when they faced the same engine on the same
hardware, so every frame carries a deployment column and the app groups by it
rather than pooling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Run:
    """One policy, one repeat, one deployment."""

    path: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]

    @property
    def policy(self) -> str:
        return self.manifest.get("policy_label") or self.manifest.get("policy") or "unknown"

    @property
    def deployment(self) -> str:
        return self.manifest.get("deployment") or "single deployment"

    @property
    def experiment(self) -> str:
        return self.manifest.get("experiment") or self.path.parent.name

    @property
    def repeat(self) -> int:
        return int(self.manifest.get("repeat", 1))

    @property
    def healthy(self) -> bool:
        """False when most scrapes failed, meaning the policy decided on stale
        state and the numbers describe the workload rather than the policy."""
        return self.summary.get("signal_was_healthy") is not False


def discover(root: Path) -> list[Run]:
    """Every run bundle under a directory tree, in a stable order."""
    runs: list[Run] = []
    for summary_path in sorted(root.rglob("summary.json")):
        manifest_path = summary_path.parent / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            runs.append(
                Run(
                    path=summary_path.parent,
                    manifest=json.loads(manifest_path.read_text()),
                    summary=json.loads(summary_path.read_text()),
                )
            )
        except json.JSONDecodeError:
            # A half-written bundle from an interrupted run. Skip it rather
            # than failing the whole view.
            continue
    return runs


def runs_frame(runs: list[Run]) -> pd.DataFrame:
    """One row per run — the level at which spread is visible.

    Deliberately not pre-aggregated. Medians hide that a policy ranged from
    30% to 85% missed across three identical runs, and that spread is usually
    the most important thing on the page.
    """
    rows = []
    for r in runs:
        s = r.summary
        ttft = s.get("ttft_ms") or {}
        tbt = s.get("tbt_ms") or {}
        slo = s.get("slo") or {}
        rows.append(
            {
                "experiment": r.experiment,
                "deployment": r.deployment,
                "policy": r.policy,
                "repeat": r.repeat,
                "healthy": r.healthy,
                "offered": s.get("offered"),
                "admitted": s.get("admitted"),
                "rejected": s.get("rejected"),
                "deferred": s.get("deferred"),
                "admit_rate": s.get("admit_rate"),
                "offered_attainment": s.get("offered_attainment"),
                "served_attainment": s.get("served_attainment"),
                "goodput_rps": s.get("goodput_rps"),
                "ttft_p50": ttft.get("p50"),
                "ttft_p95": ttft.get("p95"),
                "ttft_p99": ttft.get("p99"),
                "tbt_p95": tbt.get("p95"),
                "wasted_fraction": s.get("wasted_fraction"),
                "missed_ttft": slo.get("missed_ttft"),
                "missed_itl": slo.get("missed_itl"),
                "failed_outright": slo.get("failed_outright"),
                "scrape_failures": s.get("scrape_failures"),
                "decision_lag_p95_ms": s.get("decision_lag_p95_ms"),
                "path": str(r.path),
            }
        )
    return pd.DataFrame(rows)


def summarise(frame: pd.DataFrame) -> pd.DataFrame:
    """Median per policy, with the observed range beside it.

    Range rather than a confidence interval: with three repeats a CI would
    claim precision the sample size cannot support.
    """
    if frame.empty:
        return frame

    numeric = [
        "admit_rate",
        "offered_attainment",
        "served_attainment",
        "goodput_rps",
        "ttft_p95",
        "wasted_fraction",
    ]
    grouped = frame.groupby(["deployment", "policy"], dropna=False)

    out = grouped[numeric].median().reset_index()
    out["runs"] = grouped.size().values
    for col in ("ttft_p95", "offered_attainment"):
        spread = grouped[col].agg(lambda s: (s.max() - s.min()) / 2 if len(s) > 1 else None)
        out[f"{col}_spread"] = spread.values
    return out


def decisions_frame(run: Run, limit: int = 20000) -> pd.DataFrame:
    """Every decision in one run, with the state it was made on.

    This is what makes a refusal explainable after the fact rather than merely
    counted — it carries the KV pressure and queue depth at the moment.
    """
    path = run.path / "decisions.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with path.open() as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    frame = pd.DataFrame(rows)
    if not frame.empty and "decided_at" in frame:
        # Seconds from the first decision, so runs can be overlaid.
        frame["t"] = frame["decided_at"] - frame["decided_at"].min()
    return frame


def outcomes_frame(run: Run, limit: int = 20000) -> pd.DataFrame:
    """Per-request timings for one run."""
    path = run.path / "outcomes.jsonl"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with path.open() as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(rows)


def unavailable_metrics(runs: list[Run]) -> dict[str, str]:
    """Metrics no engine can report, with the reason.

    Surfaced rather than omitted: a reader should be able to tell the
    difference between a metric that is zero and one that was never
    measurable.
    """
    for r in runs:
        block = r.summary.get("unavailable")
        if isinstance(block, dict) and block:
            return {str(k): str(v) for k, v in block.items()}
    return {}


__all__ = [
    "Run",
    "decisions_frame",
    "discover",
    "outcomes_frame",
    "runs_frame",
    "summarise",
    "unavailable_metrics",
]
