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
DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"

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


# --- branding -------------------------------------------------------------


def test_palette_matches_the_banner_asset() -> None:
    """The brand lives in docs/assets/banner.svg. If someone edits the banner,
    this fails rather than letting the app drift away from it."""
    import re
    from pathlib import Path

    import theme

    svg = (Path(__file__).resolve().parents[1] / "docs" / "assets" / "banner.svg").read_text()
    in_banner = {c.upper() for c in re.findall(r"#[0-9a-fA-F]{6}", svg)}

    assert theme.INK.upper() in in_banner
    assert theme.YELLOW.upper() in in_banner
    assert theme.WHITE.upper() in in_banner
    assert theme.GREY.upper() in in_banner
    assert "Helvetica" in theme.FONT


def test_admitted_uses_the_brand_yellow() -> None:
    """The wordmark spells "Admit" in yellow, so yellow means admitted
    throughout — one less legend to learn."""
    import theme

    assert theme.DECISION_COLORS["admit"] == theme.YELLOW
    assert theme.DECISION_COLORS["reject"] != theme.YELLOW


def test_a_policy_keeps_its_colour_across_charts() -> None:
    import theme

    first = theme.policy_color_map(["a", "b", "c"])
    again = theme.policy_color_map(["c", "b", "a"])
    assert first == again


def test_chart_theme_uses_the_brand_surface() -> None:
    """Charts on a light ground inside a dark page look borrowed from another
    application."""
    import theme

    cfg = theme.chart_theme()["config"]
    assert cfg["background"] == "transparent"
    assert theme.POLICY_COLORS[0] == theme.YELLOW


# --- pages ----------------------------------------------------------------


@pytest.mark.parametrize("page", ["design", "algorithms", "run", "results", "glossary"])
def test_every_page_renders(page: str) -> None:
    """Rendering is where the real failures live — a chart encoding that does
    not match the frame, or a column added to one page and not another. A
    plain HTTP check would call all of those healthy."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "pages" / f"{page}.py"), default_timeout=120)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]


def test_design_page_offers_every_registered_policy() -> None:
    """The form must not hard-code a policy list that drifts from the registry."""
    from streamlit.testing.v1 import AppTest

    from admitperf.core.registry import available

    app = AppTest.from_file(str(DASHBOARD / "pages" / "design.py"), default_timeout=120)
    app.run()
    offered = set(app.multiselect[0].options)
    assert set(available()) <= offered


def test_design_page_validates_with_the_same_rules_as_the_cli() -> None:
    """A setting that would fail at deploy time should fail in the form, in
    milliseconds rather than ten minutes into provisioning."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "pages" / "design.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.code)
    assert "policies" in text  # the generated YAML is shown for review


def test_the_algorithms_page_lists_every_installed_policy() -> None:
    """It reads the live registry, so installing a policy makes it appear
    without anyone editing the page."""
    from streamlit.testing.v1 import AppTest

    from admitperf.core.registry import available

    app = AppTest.from_file(str(DASHBOARD / "pages" / "algorithms.py"), default_timeout=120)
    app.run()
    text = " ".join(e.label for e in app.expander)
    for name in available():
        assert name in text, f"{name} is installed but not shown"


def test_the_glossary_covers_the_terms_that_appear_in_results() -> None:
    """A term shown in a chart and missing from the glossary is the case worth
    catching — that is exactly where someone goes looking."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "pages" / "glossary.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown).lower()

    for term in ("ttft", "goodput", "warmup", "deployment", "kv cache", "p95"):
        assert term in text, f"{term} is used in the app but not explained"
    for reason in ("kv_pressure", "queue_depth", "deadline_unmeetable", "no_signal"):
        assert reason in text, f"rejection reason {reason} is unexplained"
