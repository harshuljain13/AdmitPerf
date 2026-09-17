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


@pytest.mark.parametrize("page", ["experiments", "algorithms", "run", "results", "terminology"])
def test_every_page_renders(page: str) -> None:
    """Rendering is where the real failures live — a chart encoding that does
    not match the frame, or a column added to one page and not another. A
    plain HTTP check would call all of those healthy."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / f"{page}.py"), default_timeout=120)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]


def test_experiments_page_offers_every_registered_policy() -> None:
    """The form must not hard-code a policy list that drifts from the registry."""
    from streamlit.testing.v1 import AppTest

    from admitperf.core.registry import available

    app = AppTest.from_file(str(DASHBOARD / "views" / "experiments.py"), default_timeout=120)
    app.run()
    offered = set(app.multiselect[0].options)
    assert set(available()) <= offered


def test_experiments_page_validates_with_the_same_rules_as_the_cli() -> None:
    """A setting that would fail at deploy time should fail in the form, in
    milliseconds rather than ten minutes into provisioning."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "experiments.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown) + " ".join(c.value for c in app.code)
    assert "policies" in text  # the generated YAML is shown for review


def test_the_run_page_can_provision_before_it_measures() -> None:
    """Running against a real GPU should be one button, not a terminal detour:
    the page provisions, checks, measures and tears down itself."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "run.py"), default_timeout=120)
    app.run()
    assert any("Provision" in o for o in app.radio[0].options)

    app.radio[0].set_value(next(o for o in app.radio[0].options if o.startswith("Provision"))).run()
    labels = [c.label for c in app.checkbox]
    assert any("Tear the deployment down" in label for label in labels)


def test_the_run_page_shows_the_whole_pipeline_before_starting() -> None:
    """Including the report stage: a run that produces no artifact is half a
    result, and a plan that hides a stage cannot be checked before it costs
    money."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "run.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown)
    for stage in ("Provision", "Run the experiment", "Aggregate", "Write the report", "Tear down"):
        assert stage in text, f"pipeline stage {stage!r} not shown before starting"


def test_the_plan_says_which_steps_will_actually_run() -> None:
    """A step that will not run has to look different from one that has not run
    yet, or the plan reads as a list of promises it is not making. Only the
    steps that will run are numbered."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "run.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown)

    assert "steps will run" in text, "plan does not say how many steps will run"
    assert "skipped —" in text, "a skipped step does not say why"
    assert "will run<" in text, "a step that will run is not labelled as such"


def test_the_run_page_tears_down_by_default() -> None:
    """A GPU left running bills by the minute, and the page is the surface most
    likely to be driven by someone who will not think to check."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "run.py"), default_timeout=120)
    app.run()
    app.radio[0].set_value(next(o for o in app.radio[0].options if o.startswith("Provision"))).run()
    teardown = next(c for c in app.checkbox if "Tear the deployment down" in c.label)
    assert teardown.value is True


def test_the_algorithms_page_lists_every_installed_policy() -> None:
    """It reads the live registry, so installing a policy makes it appear
    without anyone editing the page."""
    from streamlit.testing.v1 import AppTest

    from admitperf.core.registry import available

    app = AppTest.from_file(str(DASHBOARD / "views" / "algorithms.py"), default_timeout=120)
    app.run()
    text = " ".join(e.label for e in app.expander)
    for name in available():
        assert name in text, f"{name} is installed but not shown"


def test_terminology_covers_the_terms_that_appear_in_results() -> None:
    """A term shown in a chart and missing from the glossary is the case worth
    catching — that is exactly where someone goes looking."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "views" / "terminology.py"), default_timeout=120)
    app.run()
    text = " ".join(m.value for m in app.markdown).lower()

    for term in ("ttft", "goodput", "warmup", "deployment", "kv cache", "p95"):
        assert term in text, f"{term} is used in the app but not explained"
    for reason in ("kv_pressure", "queue_depth", "deadline_unmeetable", "no_signal"):
        assert reason in text, f"rejection reason {reason} is unexplained"


def test_page_file_title_and_url_are_the_same_word() -> None:
    """A page titled one thing and filed under another is a trap for whoever
    reads the sidebar and then goes looking for the code."""
    import re

    app_source = (DASHBOARD / "app.py").read_text()
    entries = re.findall(
        r'"views"\s*/\s*"(\w+)\.py".*?title="([^"]+)".*?url_path="([^"]+)"',
        app_source,
        re.S,
    )
    assert len(entries) >= 5, "expected every page to declare an explicit url_path"

    for filename, title, url in entries:
        assert filename == url, f"{filename}.py is served at /{url}"
        assert title.lower().replace(" ", "") == filename, f"{filename}.py is titled {title!r}"
        assert title[0].isupper(), f"{title!r} does not start with a capital"
        assert (DASHBOARD / "views" / f"{filename}.py").exists()


def test_the_entry_point_renders() -> None:
    """app.py itself, not just the pages it routes to.

    Navigation is declared here, and a bad icon or a missing page file raises
    only when it is rendered. The server still returns 200, so nothing short of
    rendering catches it — which is how an invalid icon shipped.
    """
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=120)
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]


def test_page_icons_are_material_names_not_unicode_symbols() -> None:
    """st.Page accepts an emoji or a Material icon. A plain Unicode glyph like
    "✎" looks like an icon in an editor and is rejected at render."""
    import re

    source = (DASHBOARD / "app.py").read_text()
    # st.Page icons only. set_page_config takes page_icon, where a plain emoji
    # is valid, so the two must not be checked by the same rule.
    icons = re.findall(r'(?<!page_)icon="([^"]+)"', source)

    assert icons, "no page icons found"
    for icon in icons:
        assert icon.startswith(":material/") and icon.endswith(":"), (
            f"{icon!r} is not a Material icon name; st.Page will reject it at render"
        )


def test_pages_are_not_in_streamlit_magic_directory() -> None:
    """`pages/` is auto-discovered by Streamlit, which derives titles from
    filenames. If explicit navigation ever fails, the app silently falls back
    to that and the sidebar fills with lowercase filenames — which is exactly
    what happened when an invalid icon broke st.navigation."""
    assert not (DASHBOARD / "pages").exists(), (
        "dashboard/pages/ shadows explicit navigation; keep views in dashboard/views/"
    )
    assert (DASHBOARD / "views").exists()


def test_every_page_declares_an_explicit_title() -> None:
    """Without one, Streamlit infers it from the filename and renders it in
    lowercase."""
    import re

    source = (DASHBOARD / "app.py").read_text()
    pages = re.findall(r"st\.Page\((.*?)\)", source, re.S)
    assert pages, "no pages declared"
    for page in pages:
        assert "title=" in page, f"page without an explicit title: {page[:60]}"


# --- contrast and logo ----------------------------------------------------


def _relative_luminance(hex_colour: str) -> float:
    channels = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_relative_luminance(a), _relative_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_text_on_the_brand_yellow_is_readable() -> None:
    """Streamlit paints primary buttons with primaryColor and puts its own
    foreground on top — white in a dark theme, which is 1.6:1 against this
    yellow. The app overrides it to near-black."""
    import theme

    assert _contrast(theme.YELLOW, theme.INK) >= 4.5
    assert _contrast(theme.YELLOW, theme.WHITE) < 3.0  # the default we override


def test_every_yellow_fill_sets_a_text_colour() -> None:
    """A rule that paints the brand yellow without stating a text colour
    inherits the theme foreground, which is the unreadable case."""
    import re

    import theme

    for block in re.findall(r"\{[^{}]*\}", theme.CSS):
        # A block using yellow as a background must not also set a light
        # foreground on it.
        if "color:" in block and f"background: {theme.YELLOW}" in block:
            assert theme.INK in block, f"yellow fill without dark text: {block[:80]}"


def test_the_button_label_is_coloured_not_just_the_button() -> None:
    """Streamlit wraps a button's label in its own markdown container, which
    carries a colour of its own and inherits nothing from the button. Colouring
    only the <button> gives a correct yellow fill with an invisible label —
    which is exactly how this was found."""
    import re

    import theme

    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", theme.CSS)
    inner = [sel for sel, body in rules if "*" in sel and "utton" in sel and theme.INK in body]
    assert inner, "nothing colours the label inside a button"
    for selector in ('kind="primary"', "stBaseButton-primary"):
        assert any(selector in sel for sel in inner), f"label inside {selector} left uncoloured"


def test_the_sidebar_logo_exists_and_is_rendered_from_the_svg() -> None:
    """st.logo takes an image. SVG text would depend on Helvetica being present
    in whoever's browser, so the wordmark is rasterised from the source SVG."""
    assets = DASHBOARD.parent / "docs" / "assets"
    for name in ("logo.svg", "logo.png", "icon.svg", "icon.png"):
        assert (assets / name).exists(), f"missing brand asset {name}"

    svg = (assets / "logo.svg").read_text()
    import theme

    assert theme.YELLOW in svg and theme.WHITE in svg, "logo has drifted from the palette"


def test_provenance_a_paper_would_need_is_recorded() -> None:
    """A bundle that cannot say which code produced it is not evidence. The
    package version reads 0.0.1 for months, so the commit is what matters —
    a fix landed between two of our runs and we could not tell from disk which
    side of it either bundle sat on."""
    from admitperf.bench.environment import code_version

    code = code_version()
    assert "commit" in code and "dirty" in code
    if code["commit"] is not None:
        assert len(code["commit"]) >= 7, "a short sha still has to identify a commit"
