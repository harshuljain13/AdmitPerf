"""Cross-run comparison.

The job here is to stop a reader believing a difference that is smaller than
the run-to-run noise. These tests pin the reporting of spread and the warning
when there is only one sample.
"""

from __future__ import annotations

import json
from pathlib import Path

from admitperf.bench.compare import compare_dir, load_runs


def _run(root: Path, name: str, label: str, *, ttft_p95: float, admitted: int, goodput: float):
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"policy_label": label, "policy": label}))
    (d / "summary.json").write_text(
        json.dumps(
            {
                "offered": 100,
                "admitted": admitted,
                "goodput_under_admission": goodput,
                "ttft_ms": {"p50": ttft_p95 / 2, "p95": ttft_p95, "p99": ttft_p95},
                "reject_reasons": {"kv_pressure": 100 - admitted} if admitted < 100 else {},
            }
        )
    )


def test_empty_directory_reports_nothing(tmp_path: Path) -> None:
    assert compare_dir(tmp_path) is None


def test_runs_group_by_policy(tmp_path: Path) -> None:
    _run(tmp_path, "a-r1", "no_admission", ttft_p95=200, admitted=100, goodput=1.0)
    _run(tmp_path, "a-r2", "no_admission", ttft_p95=220, admitted=100, goodput=1.0)
    _run(tmp_path, "b-r1", "kv_threshold", ttft_p95=80, admitted=50, goodput=0.5)

    assert len(load_runs(tmp_path)) == 3
    out = compare_dir(tmp_path)
    assert out is not None
    assert "2 policies" in out
    assert "no_admission" in out and "kv_threshold" in out


def test_spread_is_reported_across_repeats(tmp_path: Path) -> None:
    _run(tmp_path, "r1", "p", ttft_p95=100, admitted=100, goodput=1.0)
    _run(tmp_path, "r2", "p", ttft_p95=140, admitted=100, goodput=1.0)

    out = compare_dir(tmp_path) or ""
    assert "120ms" in out  # median
    assert "±20" in out  # half the range


def test_single_run_warns_instead_of_implying_precision(tmp_path: Path) -> None:
    _run(tmp_path, "r1", "solo", ttft_p95=100, admitted=100, goodput=1.0)
    out = compare_dir(tmp_path) or ""
    assert "only one run" in out
    assert "--repeats" in out


def test_reject_reasons_are_totalled(tmp_path: Path) -> None:
    _run(tmp_path, "r1", "shed", ttft_p95=50, admitted=40, goodput=0.4)
    _run(tmp_path, "r2", "shed", ttft_p95=50, admitted=30, goodput=0.3)
    out = compare_dir(tmp_path) or ""
    assert "kv_pressure=130" in out  # 60 + 70


def test_bundles_without_a_manifest_are_skipped(tmp_path: Path) -> None:
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "summary.json").write_text(json.dumps({"offered": 1}))
    assert load_runs(tmp_path) == []
