"""The dashboard's data layer.

Charts are not tested; the loading and aggregation behind them is. The failure
worth guarding against is a half-written or older bundle taking the whole view
down — results accumulate over months and older ones will not have every field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from data import discover, runs_frame, summarise, unavailable_metrics  # noqa: E402


def _bundle(root: Path, name: str, *, policy: str, deployment: str | None = None, **summary):
    d = root / name
    d.mkdir(parents=True)
    manifest = {"policy_label": policy, "policy": policy, "experiment": "x", "repeat": 1}
    if deployment:
        manifest["deployment"] = deployment
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "summary.json").write_text(
        json.dumps(
            {
                "offered": 100,
                "admitted": 60,
                "rejected": 40,
                "admit_rate": 0.6,
                "offered_attainment": 0.5,
                "served_attainment": 0.83,
                "goodput_rps": 4.2,
                "ttft_ms": {"p50": 100, "p95": 200, "p99": 300},
                "reject_reasons": {"queue_depth": 40},
                **summary,
            }
        )
    )
    return d


def test_finds_bundles(tmp_path: Path) -> None:
    _bundle(tmp_path, "a", policy="p1")
    _bundle(tmp_path, "b", policy="p2")
    assert len(discover(tmp_path)) == 2


def test_a_bundle_without_a_manifest_is_skipped(tmp_path: Path) -> None:
    """An interrupted run leaves one behind; it should not take the view down."""
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "summary.json").write_text("{}")
    assert discover(tmp_path) == []


def test_corrupt_json_is_skipped_not_raised(tmp_path: Path) -> None:
    d = _bundle(tmp_path, "good", policy="p1")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json")
    (bad / "summary.json").write_text("{}")

    runs = discover(tmp_path)
    assert len(runs) == 1 and runs[0].path == d


def test_older_bundles_missing_new_fields_do_not_break(tmp_path: Path) -> None:
    """Results accumulate across versions. A bundle written before offered
    attainment existed should appear with a gap, not crash the page."""
    d = tmp_path / "old"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"policy": "old", "repeat": 1}))
    (d / "summary.json").write_text(json.dumps({"offered": 10, "goodput_under_admission": 0.3}))

    frame = runs_frame(discover(tmp_path))
    assert len(frame) == 1
    assert frame.iloc[0]["offered_attainment"] is None or pd_isna(
        frame.iloc[0]["offered_attainment"]
    )


def pd_isna(v) -> bool:
    import pandas as pd

    return bool(pd.isna(v))


def test_runs_frame_keeps_one_row_per_run(tmp_path: Path) -> None:
    """Not pre-aggregated: medians hide the spread, which is usually the most
    important thing on the page."""
    _bundle(tmp_path, "r1", policy="p")
    _bundle(tmp_path, "r2", policy="p")
    assert len(runs_frame(discover(tmp_path))) == 2


def test_summarise_groups_by_deployment_not_just_policy(tmp_path: Path) -> None:
    """Pooling across hardware would report the machine as if it were the
    policy."""
    _bundle(tmp_path, "a", policy="p", deployment="a10g")
    _bundle(tmp_path, "b", policy="p", deployment="a100")

    agg = summarise(runs_frame(discover(tmp_path)))
    assert len(agg) == 2
    assert set(agg["deployment"]) == {"a10g", "a100"}


def test_summarise_reports_spread_only_with_repeats(tmp_path: Path) -> None:
    _bundle(tmp_path, "r1", policy="p", ttft_ms={"p50": 90, "p95": 100, "p99": 110})
    agg = summarise(runs_frame(discover(tmp_path)))
    assert pd_isna(agg.iloc[0]["ttft_p95_spread"])

    _bundle(tmp_path, "r2", policy="p", ttft_ms={"p50": 90, "p95": 200, "p99": 210})
    agg = summarise(runs_frame(discover(tmp_path)))
    assert agg.iloc[0]["ttft_p95_spread"] == pytest.approx(50.0)


def test_degraded_runs_are_marked(tmp_path: Path) -> None:
    """A run whose scrapes mostly failed describes the workload, not the policy."""
    _bundle(tmp_path, "ok", policy="p1")
    _bundle(tmp_path, "bad", policy="p2", signal_was_healthy=False)

    frame = runs_frame(discover(tmp_path))
    assert set(frame["healthy"]) == {True, False}


def test_unavailable_metrics_are_surfaced(tmp_path: Path) -> None:
    """So a reader can tell a metric that is zero from one never obtainable."""
    _bundle(
        tmp_path, "a", policy="p", unavailable={"preemption_loss_bytes": "no engine reports it"}
    )
    assert "preemption_loss_bytes" in unavailable_metrics(discover(tmp_path))


def test_empty_directory_yields_an_empty_frame(tmp_path: Path) -> None:
    assert runs_frame(discover(tmp_path)).empty
