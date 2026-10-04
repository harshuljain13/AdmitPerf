"""The dashboard: logs and their findings, in a browser.

It READS. Nothing here produces a log, runs a policy, or touches a cluster — the
CLI and your gateway do that. A dashboard that asks you to upload the artifact it
exists to display has the arrows pointing the wrong way.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from admitperf.core.log import Log
from admitperf.report import Report

YELLOW = "#F5C518"
INK = "#1a1a1a"

VERDICT = {
    "LIVE": (YELLOW, INK, "The policy fired, so this log can support a claim about it."),
    "INERT": (
        "#E06C4F",
        "#ffffff",
        "The policy refused nothing. These numbers are indistinguishable from no "
        "policy at all — the fix is usually the load, not the policy.",
    ),
    "UNKNOWN": (
        "#6b6b6b",
        "#ffffff",
        "No policy ran. This log says what the cluster was doing, which is what you "
        "need to choose a signal.",
    ),
}


def find_logs(root: Path) -> list[Path]:
    """Every .jsonl under the working directory, newest first. Discovery rather than
    a file picker: these were written here, so asking for them is asking you to do
    the dashboard's job."""
    found = [p for p in root.rglob("*.jsonl") if ".venv" not in p.parts]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def main() -> None:
    st.set_page_config(page_title="AdmitPerf", page_icon="◆", layout="wide")
    st.markdown(
        f"<h1 style='color:{INK}'>Admit<span style='color:{YELLOW}'>Perf</span></h1>",
        unsafe_allow_html=True,
    )

    logs = find_logs(Path.cwd())
    if not logs:
        st.info(
            "No logs found here. Record one first:\n\n"
            "`admitperf watch http://localhost:8000/metrics --for 5m -o trace.jsonl`"
        )
        return

    choice = st.sidebar.selectbox("Log", logs, format_func=lambda p: str(p.relative_to(Path.cwd())))
    report = Report.from_log(choice)

    # --- the finding, before any other number ---------------------------
    verdict = report.verdict()
    bg, fg, meaning = VERDICT[verdict]
    st.markdown(
        f"<div style='background:{bg};color:{fg};padding:1rem 1.25rem;border-radius:6px'>"
        f"<b style='font-size:1.4rem'>{verdict}</b><br/>{meaning}</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    cols = st.columns(4)
    cols[0].metric("Decisions", len(report.decisions))
    cols[1].metric("Refused", report.refused)
    cols[2].metric("State samples", len(report.states))
    cols[3].metric("Outcomes", len(report.outcomes))

    if report.policy:
        st.caption(f"policy `{report.policy}` · params `{report.params}`")

    # --- which signals moved --------------------------------------------
    st.subheader("Which signals moved")
    st.caption(
        "Every signal is recorded per decision, not only the one the policy read — "
        "so a flat signal is distinguishable from a quiet cluster, and you can see "
        "whether a different signal would have fired."
    )
    rows = []
    for name, r in report.signals().items():
        rows.append(
            {"signal": name, "min": "—", "p50": "—", "p95": "—", "max": "—", "n": 0}
            if r is None
            else {
                "signal": name,
                "min": round(r["min"], 4),
                "p50": round(r["p50"], 4),
                "p95": round(r["p95"], 4),
                "max": round(r["max"], 4),
                "n": r["samples"],
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    thr = report.threshold
    if thr is not None:
        st.caption(
            f"The policy's threshold was **{thr:g}**. A signal whose max never reached "
            "it could not have made this policy fire, whatever else the numbers say."
        )
    else:
        st.caption("`—` is absent, not zero. Reading a missing signal as 0.0 looks like headroom.")

    # --- refusals -------------------------------------------------------
    if by_reason := report.by_reason():
        st.subheader("Why requests were refused")
        st.bar_chart(by_reason, horizontal=True, color=YELLOW)
        st.caption(
            "Reasons come from a controlled vocabulary, so these group with anyone "
            "else's — which is the whole point of a standard."
        )

    # --- what it cost ---------------------------------------------------
    st.subheader("What it cost")
    st.code("\n".join(report.cost_text()), language=None)

    # --- the two configs, beside the finding -----------------------------
    with st.expander("The full text report"):
        st.code(report.text(), language=None)
    with st.expander(f"Raw log — {len(Log.read(choice))} records"):
        st.json(Log.read(choice)[:20])


main()
