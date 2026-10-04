"""The dashboard must render.

This file exists because of a failure I shipped: `main()` called `report_view(exp)`
while the function had grown a second parameter, and I checked the app with `curl` and
got HTTP 200. A 200 means the server started. The exception happened on render, in the
browser, where nothing I was looking at could see it.

`AppTest` runs the script the way Streamlit does and surfaces exceptions, so a
signature drift or a missing key fails here instead of in front of someone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

st_testing = pytest.importorskip(
    "streamlit.testing.v1", reason="the dashboard is an optional extra"
)

from admitperf.policies import KvThreshold, NoAdmission  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "admitperf" / "dashboard" / "app.py"

BUSY = {"vllm:kv_cache_usage_perc": 0.95, "vllm:num_requests_waiting": 40.0}
IDLE = {"vllm:kv_cache_usage_perc": 0.05, "vllm:num_requests_waiting": 0.0}


def _logs(root: Path, repeats: int = 2) -> None:
    """A named experiment with a baseline and one policy, which is the smallest thing
    the comparison view can work with."""
    ident = {"experiment": "test-exp", "notes": "a fixture"}
    for repeat in range(1, repeats + 1):
        for sub, build in (
            ("baseline", lambda log, r: NoAdmission(log=log, run=r, **ident)),
            ("policy", lambda log, r: KvThreshold(threshold=0.9, log=log, run=r, **ident)),
        ):
            d = root / "runs" / sub
            d.mkdir(parents=True, exist_ok=True)
            with build(str(d / f"r{repeat}.jsonl"), f"r{repeat}") as p:
                for i in range(12):
                    rid = f"q{i}"
                    if p(BUSY if i % 2 else IDLE, request_id=rid).admitted:
                        p.outcome(rid, ttft_ms=100.0 + i * 10, ok=i < 8)


def _run(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    at = st_testing.AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    return at


def test_it_renders_with_no_logs_and_says_what_to_do(tmp_path, monkeypatch) -> None:
    """The first thing a new user sees. It must not be a traceback, and it must name
    the command that produces something to look at."""
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, at.exception
    assert any("admitperf demo" in str(i.value) for i in at.info)


def test_the_one_run_view_renders(tmp_path, monkeypatch) -> None:
    """The view that was broken. `main()` passed one argument to a function that took
    two, and nothing short of rendering catches that."""
    _logs(tmp_path)
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Signal liveness" in body
    assert "seven reporting items" in body


def test_the_comparison_section_renders(tmp_path, monkeypatch) -> None:
    """Reached from the sidebar, with the baseline fixed rather than chosen."""
    _logs(tmp_path)
    at = _run(tmp_path, monkeypatch)
    assert not at.exception

    next(b for b in at.sidebar.button if b.label == "Compare").click().run()
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "spread across runs" in body
    # The baseline is not a choice; only the policy held against it is.
    labels = {s.label for s in at.selectbox}
    assert "Compared with" in labels


def test_the_terminology_view_renders() -> None:
    """Rendered directly rather than through the navigation.

    `AppTest.switch_page` only drives file-based pages, and these are function pages —
    so the nav itself is checked separately and the view is exercised here. It needs no
    logs: someone reading the definitions has not necessarily run anything yet.
    """
    # from_function strips the module's globals, so `st` is undefined inside the view.
    # A one-line script that imports it keeps them.
    at = st_testing.AppTest.from_string(
        "from admitperf.dashboard.app import terminology_view\nterminology_view()\n",
        default_timeout=30,
    )
    at.run()
    assert not at.exception, at.exception

    body = " ".join(str(m.value) for m in at.markdown)
    assert "Terminology" in body
    # The pair that causes the most confusion, and the reason the page exists.
    assert "offered" in body and "goodput" in body
    assert "survey" in body, "survey-sourced definitions should be tagged as such"


def test_the_wordmark_is_the_first_thing_in_the_sidebar(tmp_path, monkeypatch) -> None:
    """Both the wordmark and the nav are ours rather than Streamlit's built-ins.

    `st.logo` and `st.navigation` render into a slot ABOVE anything written to the
    sidebar body, so with either of them the wordmark could only ever be second.
    Emitting both ourselves makes the order on screen the order the calls run in, and
    that is what this pins.
    """
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, at.exception

    first = str(at.sidebar.markdown[0].value)
    assert "Admit" in first and "Perf" in first
    # Three sections. Comparing is its own, not a toggle buried inside Experiments:
    # "what did this run do" and "what did the policy buy" are different questions.
    assert [b.label for b in at.sidebar.button] == ["Experiments", "Compare", "Terminology"]


def test_navigation_switches_section(tmp_path, monkeypatch) -> None:
    """And it is navigation, not decoration."""
    at = _run(tmp_path, monkeypatch)
    next(b for b in at.sidebar.button if b.label == "Terminology").click().run()
    assert not at.exception, at.exception
    assert "#### Terminology" in " ".join(str(m.value) for m in at.markdown)


def test_the_sidebar_scopes_and_the_content_bar_selects(tmp_path, monkeypatch) -> None:
    """The division that fixed the squeeze.

    The sidebar holds what scopes the whole page — brand, section, experiment. View and
    arm sit inline above the content. A RIGHT-hand panel holding them as well left the
    charts about sixty percent of the width, which for a page whose job is a chart is
    the wrong way round.
    """
    _logs(tmp_path)
    at = _run(tmp_path, monkeypatch)
    assert not at.exception

    # With one experiment there is no dropdown: a control offering a single option is
    # chrome pretending to be a choice. The name is shown as text instead.
    assert not at.sidebar.selectbox
    assert "test-exp" in " ".join(str(m.value) for m in at.sidebar.markdown)

    inline = {s.label for s in at.selectbox} | {s.label for s in at.segmented_control}
    # Arm and Run: the Experiments section is for choosing a run and reading its stats.
    assert {"Policy", "Run"} <= inline
    # The signal picker belongs to one chart, so it is not a page-level control.
    assert "Signal" in inline


def test_changing_the_policy_changes_the_report(tmp_path, monkeypatch) -> None:
    """Proves the right-hand panel is wired to the content, rather than being decorative."""
    _logs(tmp_path)
    at = _run(tmp_path, monkeypatch)
    picker = next(s for s in at.selectbox if s.label == "Policy")
    labels = list(picker.options)
    assert len(labels) == 2

    first = " ".join(str(m.value) for m in at.markdown)
    picker.set_value(labels[1]).run()
    assert not at.exception, at.exception
    assert " ".join(str(m.value) for m in at.markdown) != first


def test_several_experiments_do_get_a_dropdown(tmp_path, monkeypatch) -> None:
    """The flip side: more than one, and choosing between them is a real choice."""
    _logs(tmp_path)
    second = tmp_path / "runs2" / "baseline"
    second.mkdir(parents=True)
    with NoAdmission(log=str(second / "r1.jsonl"), experiment="other-exp", run="r1") as p:
        for i in range(5):
            p(IDLE, request_id=f"z{i}")

    at = _run(tmp_path, monkeypatch)
    assert not at.exception, at.exception
    picker = next(s for s in at.sidebar.selectbox if s.label == "Experiment")
    assert set(picker.options) == {"test-exp", "other-exp"}
