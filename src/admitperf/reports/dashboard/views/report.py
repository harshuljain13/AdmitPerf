"""Reports produced by `admitperf run`.

The CLI runs experiments and writes `report.json`. This page finds them and shows
them. There is nothing to upload: a dashboard that asks you to supply the artifact
it exists to display has the arrows pointing the wrong way.

It computes nothing either — no verdict, no range, no item status. Every number
here came out of the file the CLI wrote, which is the same file the text and HTML
renderers read. A page that re-derives a verdict is a second implementation of the
thing that matters, and two implementations drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from admitperf.report.schema import SCHEMA_VERSION, problems
from admitperf.reports.paths import experiments_dir

VERDICT_STYLE = {
    "LIVE": ("✅", "success"),
    "MARGINAL": ("⚠️", "warning"),
    "INERT": ("🚫", "error"),
    "UNKNOWN": ("❓", "info"),
}

st.title("Reports")


def _discover() -> dict[str, list[Path]]:
    """Every run, grouped by the experiment that produced it, newest first."""
    out: dict[str, list[Path]] = {}
    root = experiments_dir()
    if not root.is_dir():
        return out
    for q in sorted(root.rglob("report.json")):
        parts = q.relative_to(root).parts
        if parts:
            out.setdefault(parts[0], []).append(q)
    for runs in out.values():
        runs.sort(key=lambda r: r.stat().st_mtime, reverse=True)
    return out


groups = _discover()
if not groups:
    st.info(
        "No reports yet. Run an experiment:\n\n"
        "```\nadmitperf run experiments/signal-liveness --mock\n```\n\n"
        "That drives load through the policy and its baseline, and writes one "
        "`report.json` per arm."
    )
    st.stop()

st.markdown("#### 1 &nbsp;·&nbsp; Which experiment")
experiment = st.selectbox(
    "Experiment",
    sorted(groups),
    label_visibility="collapsed",
    help="Each experiment sends a defined load at a cluster config. Written by `admitperf run`.",
)

loaded: list[tuple[str, dict[str, Any]]] = []
for q in groups[experiment]:
    try:
        loaded.append((q.parent.name, json.loads(q.read_text())))
    except (OSError, json.JSONDecodeError) as exc:
        st.warning(f"{q.parent.name}: unreadable ({exc})")

major = SCHEMA_VERSION.split(".")[0]
stale = [n for n, d in loaded if str(d.get("schema_version", "0")).split(".")[0] != major]
if stale:
    st.error(
        f"{len(stale)} run(s) use an incompatible report schema and are hidden: "
        f"{', '.join(stale)}. Re-run them rather than reading them — the fields would "
        "be interpreted as something they are not."
    )
    loaded = [(n, d) for n, d in loaded if n not in stale]
if not loaded:
    st.stop()

# --- every arm at once: the comparison IS the point ------------------------
st.markdown(f"#### 2 &nbsp;·&nbsp; Its {len(loaded)} run(s)")

st.dataframe(
    {
        "run": [n for n, _ in loaded],
        "liveness": [d["liveness"]["verdict"] for _, d in loaded],
        "signal max": [d["signal"].get("max") for _, d in loaded],
        "threshold": [d["signal"].get("threshold") for _, d in loaded],
        "rejected": [d["decisions"]["rejected"] for _, d in loaded],
        "total": [d["decisions"]["total"] for _, d in loaded],
        "scrapes failed": [(d.get("harness") or {}).get("scrapes_failed") for _, d in loaded],
    },
    hide_index=True,
    use_container_width=True,
)

inert = [n for n, d in loaded if d["liveness"]["verdict"] in ("INERT", "UNKNOWN")]
if inert:
    st.warning(
        f"**{len(inert)} of {len(loaded)} run(s) could not have fired:** "
        f"{', '.join(inert)}. Those rows say nothing about the policy — the signal "
        "never reached its threshold. The usual cause is load rather than the policy: "
        "concurrency is arrival rate times request duration, so short requests cannot "
        "fill a cache at any rate."
    )

st.divider()
st.markdown("#### 3 &nbsp;·&nbsp; One run, in full")
chosen = st.selectbox("Run", [n for n, _ in loaded], label_visibility="collapsed")
payload = next(d for n, d in loaded if n == chosen)

run, live, pol, sig = (
    payload["run"],
    payload["liveness"],
    payload["policy"],
    payload["signal"],
)

# The verdict first, as everywhere else. A reader who looks at nothing else
# should still know whether the run can support a claim.
#: What the verdict means for whether this run can be cited. The verdict alone is
#: vocabulary; this says what to do with it.
#: What to DO with the verdict. Kept because the verdict alone is vocabulary, and
#: the response to an inert run is to change the load rather than the policy — which
#: is not obvious and is the mistake the project exists to prevent.
MEANS = {
    "LIVE": "Can support a claim about the policy.",
    "MARGINAL": "Cannot support a claim — the policy never fired. Load is near the "
    "transition point.",
    "INERT": "Says nothing about the policy. **Change the load, not the policy.**",
    "UNKNOWN": "Cannot be judged — no signal was recorded.",
}

st.markdown(f"### {chosen}")
icon, kind = VERDICT_STYLE.get(live["verdict"], VERDICT_STYLE["UNKNOWN"])
getattr(st, kind)(
    f"{icon} **{live['verdict']}** — {live['explanation']}.\n\n{MEANS.get(live['verdict'], '')}"
)

st.caption(
    " · ".join(str(run[k]) for k in ("id", "cluster", "model", "engine", "commit") if run.get(k))
)

left, right = st.columns(2)

with left:
    st.subheader(f"Policy — {pol['name']}")
    st.dataframe(
        {
            "axis": [
                "unit",
                "setting",
                "slo-awareness",
                "signal quantity",
                "signal structure",
                "metadata assumed",
                "portability",
            ],
            "value": [
                pol["unit"],
                pol["setting"],
                pol["slo_awareness"],
                pol.get("signal_quantity") or "none declared",
                pol.get("signal_structure", "scalar"),
                pol.get("metadata_assumed", "none"),
                f"Class {pol['portability']}",
            ],
        },
        hide_index=True,
        use_container_width=True,
    )

with right:
    st.markdown(f"**Signal — `{sig['name']}`**")
    if sig["samples"]:
        st.dataframe(
            {
                "": ["threshold", "min", "p50", "p95", "p99", "max"],
                "value": [sig.get(k) for k in ("threshold", "min", "p50", "p95", "p99", "max")],
            },
            hide_index=True,
            use_container_width=True,
        )
        st.caption(f"crossed in {sig['crossings']} of {sig['samples']} decisions")
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

st.markdown("##### Signal over the run")
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
        st.caption("1.0 = threshold")
else:
    st.caption("No signal recorded.")

st.markdown("##### Seven reporting items")
st.caption("`n/a` = the run does not carry what the item needs. Not a failure.")
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

harness = payload.get("harness") or {}
if harness:
    st.markdown("##### Harness")

    st.dataframe(
        {"": list(harness), "value": list(harness.values())},
        hide_index=True,
        use_container_width=True,
    )

found_problems = problems(payload)
if found_problems:
    st.markdown("##### Why this run should not be cited")
    for line in found_problems:
        st.warning(line)
else:
    st.success(
        "No reporting problems found. This means the run can show whether the policy "
        "worked — not that it did."
    )

with st.expander("report.json"):
    st.json(payload)
