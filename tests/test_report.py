"""The report artifact.

What is worth testing is not that HTML was produced, but that the report says
the right thing about the numbers it was given: a gap inside the noise is not
announced as a win, a degraded run is flagged above the table, and the file
carries its own figures so it survives being emailed.
"""

from __future__ import annotations

import json
from pathlib import Path

from admitperf.bench.report import build, load_bundles, render_html, write


def _bundle(root: Path, name: str, *, policy: str, attainment: float, **extra) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {
        "experiment": "demo",
        "policy": policy,
        "policy_label": policy,
        "repeat": 1,
        "repeats": 3,
        "created_at": "2026-09-16T00:00:00Z",
        "config": {
            "infra": {"model": "Qwen/Qwen2.5-0.5B-Instruct", "gpu": "A10G", "provider": "modal"},
            "workload": {"kind": "poisson", "rate": 15.0},
        },
        **extra.pop("manifest", {}),
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "summary.json").write_text(
        json.dumps(
            {
                "offered": 100,
                "admitted": 60,
                "rejected": 40,
                "offered_attainment": attainment,
                "served_attainment": 0.9,
                "goodput_rps": 4.2,
                "ttft_ms": {"p50": 100, "p95": 200, "p99": 300},
                "reject_reasons": {"kv_pressure": 40},
                **extra,
            }
        )
    )
    return d


def test_nothing_to_report_is_not_an_error(tmp_path: Path) -> None:
    assert build(tmp_path) is None
    assert write(tmp_path) is None


def test_a_half_written_bundle_is_skipped(tmp_path: Path) -> None:
    """An interrupted run should not cost you the report for the other runs."""
    _bundle(tmp_path, "good", policy="p", attainment=0.7)
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "summary.json").write_text("{}")

    assert len(load_bundles(tmp_path)) == 1


def test_a_clear_win_is_stated_as_one(tmp_path: Path) -> None:
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.40)
    _bundle(tmp_path, "b2", policy="no_admission", attainment=0.42)
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.80)
    _bundle(tmp_path, "k2", policy="kv_threshold", attainment=0.82)

    section = build(tmp_path).sections[0]
    assert section.tone == "good"
    assert "kv_threshold" in section.headline
    assert "no_admission" in section.headline


def test_a_gap_inside_the_noise_is_not_called_a_win(tmp_path: Path) -> None:
    """The mistake the whole report exists to prevent: believing a 2% gap that
    is smaller than the run-to-run spread."""
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.50)
    _bundle(tmp_path, "b2", policy="no_admission", attainment=0.70)
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.61)
    _bundle(tmp_path, "k2", policy="kv_threshold", attainment=0.63)

    section = build(tmp_path).sections[0]
    assert section.tone == "flat"
    assert "distinguishable" in section.headline


def test_one_policy_is_a_measurement_not_a_comparison(tmp_path: Path) -> None:
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8)
    section = build(tmp_path).sections[0]
    assert section.tone == "flat"
    assert "no_admission" in section.detail


def test_a_degraded_run_is_flagged(tmp_path: Path) -> None:
    """The numbers look ordinary; the policy never saw the state it decides on."""
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8, signal_was_healthy=False)
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.4)

    caveats = " ".join(build(tmp_path).sections[0].caveats)
    assert "stale" in caveats
    assert "kv_threshold" in caveats


def test_a_single_repeat_is_flagged(tmp_path: Path) -> None:
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8)
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.4)

    caveats = " ".join(build(tmp_path).sections[0].caveats)
    assert "no spread" in caveats


def test_deployments_are_reported_separately(tmp_path: Path) -> None:
    """Pooling an A10G row with an A100 row reports the machine as the policy."""
    _bundle(tmp_path, "a", policy="p", attainment=0.8, manifest={"deployment": "a10g"})
    _bundle(tmp_path, "b", policy="p", attainment=0.4, manifest={"deployment": "a100"})

    report = build(tmp_path)
    assert {s.deployment for s in report.sections} == {"a10g", "a100"}
    assert any("must not be read across" in n for n in report.notes)


def test_provenance_records_what_produced_the_numbers(tmp_path: Path) -> None:
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8)
    prov = build(tmp_path).provenance
    assert prov["model"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert "A10G" in prov["gpu"]


def test_unmeasured_metrics_are_listed_with_their_reason(tmp_path: Path) -> None:
    """So a reader can tell a metric that is zero from one never obtainable."""
    _bundle(
        tmp_path,
        "k1",
        policy="kv_threshold",
        attainment=0.8,
        unavailable={"gpu_utilization": "needs DCGM alongside the engine"},
    )
    assert "gpu_utilization" in build(tmp_path).unavailable


def test_the_html_carries_its_own_figures(tmp_path: Path) -> None:
    """A report that only renders next to its assets stops being readable the
    first time it is emailed."""
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8)
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.4)

    html = render_html(build(tmp_path))
    assert "data:image/png;base64," in html
    assert "src='figures/" not in html


def test_write_leaves_loose_pngs_for_latex(tmp_path: Path) -> None:
    _bundle(tmp_path, "k1", policy="kv_threshold", attainment=0.8)
    _bundle(tmp_path, "b1", policy="no_admission", attainment=0.4)

    path = write(tmp_path)
    assert path == tmp_path / "report.html"
    assert (tmp_path / "compare.txt").exists()
    assert list((tmp_path / "figures").glob("*.png"))


def test_a_policy_label_cannot_inject_markup(tmp_path: Path) -> None:
    _bundle(tmp_path, "x", policy="<script>alert(1)</script>", attainment=0.8)
    assert "<script>alert(1)</script>" not in render_html(build(tmp_path))


def test_the_report_palette_matches_the_dashboard(tmp_path: Path) -> None:
    """`src/` must not import the dashboard, so the brand is restated there.
    This is what stops the two drifting apart."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
    import theme

    from admitperf.bench import report

    assert (report.INK, report.YELLOW, report.GREY) == (theme.INK, theme.YELLOW, theme.GREY)
    assert report.MUTED == theme.MUTED
    assert report.RED == theme.RED


def test_signal_range_records_what_the_policy_could_see(tmp_path: Path) -> None:
    """A policy that never fires looks identical to one that found conditions
    fine. On a 0.5B, kv_used_fraction peaked at 0.005 against a 0.9 threshold —
    establishing that took a manual dig through decision logs, which is one dig
    too many for something a paper cites."""
    from admitperf.bench.results import signal_ranges
    from admitperf.core.ports import DecisionRecord
    from admitperf.core.runner import RunResult

    result = RunResult()
    for kv, waiting in ((0.0, 0), (0.004, 3), (0.002, 11)):
        result.decisions.append(
            DecisionRecord(
                request_id="r",
                tenant_id="t",
                decided_at=0.0,
                kind="admit",
                reason=None,
                http_status=None,
                state_age_s=0.0,
                kv_used_fraction=kv,
                waiting_requests=waiting,
                running_requests=1,
            )
        )

    ranges = signal_ranges(result)
    assert ranges["kv_used_fraction"] == {"min": 0.0, "max": 0.004, "samples": 3}
    assert ranges["waiting_requests"]["max"] == 11


def test_a_signal_never_reported_is_absent_not_zero(tmp_path: Path) -> None:
    """Zero claims the signal sat at the bottom of its range. Absent says the
    engine never reported it. Those are different findings."""
    from admitperf.bench.results import signal_ranges
    from admitperf.core.ports import DecisionRecord
    from admitperf.core.runner import RunResult

    result = RunResult()
    result.decisions.append(
        DecisionRecord(
            request_id="r",
            tenant_id="t",
            decided_at=0.0,
            kind="admit",
            reason=None,
            http_status=None,
            state_age_s=0.0,
            kv_used_fraction=None,
            waiting_requests=2,
            running_requests=1,
        )
    )

    ranges = signal_ranges(result)
    assert "kv_used_fraction" not in ranges
    assert "waiting_requests" in ranges
