"""The AdmitPerf Report, in the dashboard.

Reads `report.json` and displays it. It computes nothing — no verdict, no range,
no item status. Every number here came out of the same file the text and HTML
renderers use.

That restraint is the point. A dashboard that recomputes a verdict is a second
implementation of the thing that matters, and two implementations drift. This
repository has already shipped that bug once, when the dashboard resolved its
asset path independently of the test that checked it and the two silently
disagreed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from admitperf.report.schema import SCHEMA_VERSION, problems

VERDICT_STYLE = {
    "LIVE": ("✅", "success"),
    "MARGINAL": ("⚠️", "warning"),
    "INERT": ("🚫", "error"),
    "UNKNOWN": ("❓", "info"),
}

st.title("AdmitPerf Report")
st.caption(
    "Rendered from `report.json`. Nothing on this page is recomputed — the verdict, "
    "the range and the item statuses all come out of the file."
)


def _find_reports() -> list[Path]:
    """Any report.json under experiments/ or results/, newest first."""
    root = Path(__file__).resolve().parents[4]
    found: list[Path] = []
    for base in ("experiments", "results", "runs"):
        d = root / base
        if d.is_dir():
            found += d.rglob("report.json")
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


uploaded = st.file_uploader("report.json", type="json")
payload: dict[str, Any] | None = None

if uploaded is not None:
    payload = json.load(uploaded)
else:
    found = _find_reports()
    if found:
        root = Path(__file__).resolve().parents[4]
        pick = st.selectbox(
            "or pick one from this repository",
            found,
            format_func=lambda p: str(p.relative_to(root)),
        )
        payload = json.loads(pick.read_text())

if payload is None:
    st.info(
        "No report yet. Generate one with:\n\n"
        "```\nadmitperf report decisions.jsonl --policy kv_threshold "
        "--format json -o report.json\n```"
    )
    st.stop()

got = str(payload.get("schema_version", "0"))
if got.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
    st.error(
        f"This report is schema {got}; the dashboard reads {SCHEMA_VERSION}. "
        "Major versions are not compatible, so the numbers below would be wrong."
    )
    st.stop()

run, live, pol, sig = (
    payload["run"],
    payload["liveness"],
    payload["policy"],
    payload["signal"],
)

# The verdict first, as everywhere else. A reader who looks at nothing else
# should still know whether the run can support a claim.
icon, kind = VERDICT_STYLE.get(live["verdict"], VERDICT_STYLE["UNKNOWN"])
getattr(st, kind)(f"{icon} **SIGNAL LIVENESS: {live['verdict']}** — {live['explanation']}")

bits = [run["id"]] + [str(run[k]) for k in ("cluster", "model", "engine", "commit") if run.get(k)]
st.caption(" · ".join(bits))

left, right = st.columns(2)

with left:
    st.subheader(f"Policy — {pol['name']}")
    st.dataframe(
        {
            "axis": ["unit", "setting", "objective", "signal", "portability"],
            "value": [
                pol["unit"],
                pol["setting"],
                pol["objective"],
                pol.get("signal") or "none declared",
                f"Class {pol['portability']}",
            ],
        },
        hide_index=True,
        use_container_width=True,
    )

with right:
    st.subheader(f"Signal — {sig['name']}")
    if sig["samples"]:
        st.dataframe(
            {
                "": ["threshold", "min", "p50", "p95", "p99", "max"],
                "value": [sig.get(k) for k in ("threshold", "min", "p50", "p95", "p99", "max")],
            },
            hide_index=True,
            use_container_width=True,
        )
        st.caption(f"crossed the threshold in {sig['crossings']} of {sig['samples']} decisions")
        if sig.get("missing"):
            st.warning(
                f"{sig['missing']} decisions recorded no value, so the range above is "
                "computed from a partial run."
            )
    else:
        st.warning("No signal values were recorded.")

d = payload["decisions"]
a, b, c = st.columns(3)
a.metric("Decisions", d["total"])
b.metric("Rejected", d["rejected"])
c.metric("Deferred", d["deferred"])

st.subheader("Signal over the run")
# `samples`, not the sparkline string: the no-signal sentinel is truthy and its
# spaces parse as level zero, which would plot a flat line that looks measured.
if sig.get("samples") and sig.get("sparkline"):
    levels = [" ▁▂▃▄▅▆▇█".index(ch) / 8 for ch in sig["sparkline"] if ch in " ▁▂▃▄▅▆▇█"]
    if levels:
        # y-axis fixed to 0..1 where 1 is the threshold, matching the other
        # renderers. Letting the chart autoscale would stretch a flat run to fill
        # the plot and make an inert signal look like a working one.
        st.line_chart(
            {"signal / threshold": levels, "threshold": [1.0] * len(levels)},
            height=220,
        )
        st.caption("1.0 = the threshold. A flat line near zero is an inert run.")
else:
    st.caption("No signal was recorded, so there is nothing to plot.")

st.subheader("Seven reporting items")
items = payload.get("reporting_items", [])
st.dataframe(
    {
        "#": [i["number"] for i in items],
        "item": [i["title"] for i in items],
        "status": [i["status"] for i in items],
        "detail": [i["detail"] for i in items],
    },
    hide_index=True,
    use_container_width=True,
)

found_problems = problems(payload)
if found_problems:
    st.subheader("Why this run should not be cited")
    for line in found_problems:
        st.warning(line)
else:
    st.success(
        "No reporting problems found. This means the run can show whether the policy "
        "worked — not that it did."
    )

with st.expander("report.json"):
    st.json(payload)
