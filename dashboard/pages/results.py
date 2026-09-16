"""Results, organised around the questions a reader actually arrives with.

The previous version led with a metrics table, which answers "what were the
values" — a question nobody has. It is ordered by question instead:

    1. Which policy should I use, and by how much?
    2. Can I trust that?
    3. What did refusing cost?
    4. Why did it refuse?
    5. Show me everything.

The verdict is stated in a sentence before any chart, and any reason to
distrust it is stated *above* the answer rather than in a footnote.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components import empty_state, note, section, verdict, warn  # noqa: E402
from theme import DECISION_COLORS, GREY, MUTED, WHITE, YELLOW, policy_color_map  # noqa: E402

from data import decisions_frame, discover, runs_frame, summarise, unavailable_metrics  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"


def _pct(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:.0f}%"


def _ms(x: float | None) -> str:
    return "—" if x is None or pd.isna(x) else f"{x:,.0f}ms"


def render() -> None:
    from theme import HEADER

    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Results")

    if not RESULTS.exists() or not any(RESULTS.rglob("summary.json")):
        empty_state(
            "No results yet",
            "Design an experiment, then run it. Results appear here automatically.",
        )
        return

    folders = sorted(
        (d for d in RESULTS.iterdir() if d.is_dir() and any(d.rglob("summary.json"))),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    last = st.session_state.get("last_results")
    names = [d.name for d in folders]
    idx = names.index(Path(last).name) if last and Path(last).name in names else 0

    c1, c2 = st.columns([3, 1])
    chosen = c1.selectbox("Experiment", names, index=idx)
    root = RESULTS / chosen

    runs = discover(root)
    frame = runs_frame(runs)
    if frame.empty:
        empty_state("Nothing readable in this folder", "The bundles may be incomplete.")
        return

    deployments = sorted(frame["deployment"].dropna().unique())
    deployment = (
        c2.selectbox("Deployment", deployments)
        if len(deployments) > 1
        else deployments[0]
        if deployments
        else None
    )
    if deployment:
        frame = frame[frame["deployment"] == deployment]

    agg = summarise(frame).sort_values("offered_attainment", ascending=False)
    colors = policy_color_map(sorted(frame["policy"].unique()))

    # --- 2. can I trust it? (stated before the answer) --------------------

    problems = []
    if (~frame["healthy"]).any():
        n = int((~frame["healthy"]).sum())
        problems.append(
            f"<b>{n} run(s) had most engine scrapes fail.</b> The policy was "
            "deciding on stale state, so those numbers describe the workload "
            "rather than the policy."
        )
    thin = agg[agg["runs"] < 2]["policy"].tolist()
    if thin:
        problems.append(
            f"<b>Only one run for {', '.join(thin)}.</b> No spread, so a small "
            "difference cannot be told apart from noise."
        )
    wide = agg[agg["offered_attainment_spread"].fillna(0) > 0.1]["policy"].tolist()
    if wide:
        problems.append(
            f"<b>{', '.join(wide)} varied widely across repeats.</b> The median "
            "hides how unstable it was."
        )
    if problems:
        warn("<br><br>".join(problems))

    # --- 1. the answer ----------------------------------------------------

    if agg.empty or agg["offered_attainment"].isna().all():
        warn("No run recorded SLO attainment — these bundles predate that metric.")
        best = None
    else:
        best = agg.iloc[0]
        base = agg[agg["policy"] == "no_admission"]
        if not base.empty and pd.notna(base.iloc[0]["offered_attainment"]):
            b = float(base.iloc[0]["offered_attainment"])
            gain = float(best["offered_attainment"]) - b
            if best["policy"] == "no_admission":
                verdict(
                    "No policy beat doing nothing.",
                    f"The baseline met {_pct(b)} of arriving requests' deadlines. "
                    "Everything else refused traffic it could have served — at "
                    "this load, admission control is not paying for itself. Try a "
                    "higher arrival rate or a tighter capacity limit.",
                    tone="flat",
                )
            else:
                lat = best["ttft_p95"]
                basel = (
                    float(base.iloc[0]["ttft_p95"]) if pd.notna(base.iloc[0]["ttft_p95"]) else None
                )
                speed = (
                    f" and cut slow-request latency from {_ms(basel)} to {_ms(lat)}"
                    if basel
                    else ""
                )
                verdict(
                    f"{best['policy']} served {_pct(best['offered_attainment'])} "
                    f"of arriving requests on time.",
                    f"That is {gain * 100:+.0f} points against doing nothing "
                    f"({_pct(b)}){speed}. It admitted {_pct(best['admit_rate'])} "
                    "of traffic — the rest was refused, and refusing counts as a "
                    "miss, so this is a real gain rather than a bookkeeping one.",
                )
        else:
            verdict(
                f"{best['policy']} led at {_pct(best['offered_attainment'])}.",
                "No baseline in this experiment, so there is nothing to say how "
                "much of that is the policy. Add <code>no_admission</code>.",
                tone="flat",
            )

    tabs = st.tabs(["Comparison", "What refusing cost", "Why refused", "Everything"])

    # --- comparison -------------------------------------------------------

    with tabs[0]:
        section(
            "How many arriving requests were served on time",
            "Refusals count as misses, so a policy cannot improve this by "
            "refusing more — only by refusing better.",
        )
        plot = agg.dropna(subset=["offered_attainment"])
        if not plot.empty:
            bars = (
                alt.Chart(plot)
                .mark_bar(cornerRadiusEnd=2, height=26)
                .encode(
                    x=alt.X(
                        "offered_attainment:Q",
                        title=None,
                        scale=alt.Scale(domain=[0, 1]),
                        axis=alt.Axis(format="%"),
                    ),
                    y=alt.Y("policy:N", title=None, sort="-x"),
                    color=alt.Color(
                        "policy:N",
                        scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                        legend=None,
                    ),
                    tooltip=[
                        alt.Tooltip("policy:N", title="policy"),
                        alt.Tooltip("offered_attainment:Q", title="on time", format=".1%"),
                        alt.Tooltip("admit_rate:Q", title="admitted", format=".1%"),
                        alt.Tooltip("ttft_p95:Q", title="TTFT p95", format=".0f"),
                    ],
                )
                .properties(height=max(120, 40 * len(plot)))
            )
            labels = bars.mark_text(align="left", dx=6, color=WHITE, fontSize=11).encode(
                text=alt.Text("offered_attainment:Q", format=".0%"), color=alt.value(GREY)
            )
            st.altair_chart(bars + labels, use_container_width=True)

        section("The table", "One row per policy, median across repeats.")
        table = pd.DataFrame(
            {
                "Policy": agg["policy"],
                "On time": agg["offered_attainment"].map(_pct),
                "Admitted": agg["admit_rate"].map(_pct),
                "Of admitted, on time": agg["served_attainment"].map(_pct),
                "Slow-request latency": agg["ttft_p95"].map(_ms),
                "Done per second": agg["goodput_rps"].round(2),
                "Runs": agg["runs"],
            }
        )
        st.dataframe(table, width="stretch", hide_index=True)
        note(
            "<b>On time</b> counts every request that arrived. <b>Of admitted, "
            "on time</b> counts only those let in — it looks better the more a "
            "policy refuses, which is why it is never shown alone."
        )

    # --- what refusing cost ----------------------------------------------

    with tabs[1]:
        section(
            "Latency against requests served",
            "Up and to the left is better. Far left but low means refusing "
            "traffic it could have served.",
        )
        pts = frame.dropna(subset=["ttft_p95", "offered_attainment"])
        if pts.empty:
            st.info("No runs recorded both latency and attainment.")
        else:
            st.altair_chart(
                alt.Chart(pts)
                .mark_circle(size=200, opacity=0.85)
                .encode(
                    x=alt.X("ttft_p95:Q", title="slow-request latency (ms) — lower better"),
                    y=alt.Y(
                        "offered_attainment:Q",
                        title="served on time — higher better",
                        axis=alt.Axis(format="%"),
                    ),
                    color=alt.Color(
                        "policy:N",
                        scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                        legend=alt.Legend(orient="bottom", columns=2, title=None),
                    ),
                    tooltip=["policy", "repeat", "ttft_p95", "offered_attainment", "admit_rate"],
                )
                .properties(height=400)
                .interactive(),
                use_container_width=True,
            )

        section(
            "Wasted work",
            "Tokens generated for requests that missed their deadline anyway — "
            "the clearest statement of what refusing saved.",
        )
        waste = agg.dropna(subset=["wasted_fraction"])
        if waste.empty:
            st.info("Not recorded in these runs.")
        else:
            st.altair_chart(
                alt.Chart(waste)
                .mark_bar(color=YELLOW, cornerRadiusEnd=2, height=24)
                .encode(
                    x=alt.X("wasted_fraction:Q", title=None, axis=alt.Axis(format="%")),
                    y=alt.Y("policy:N", sort="-x", title=None),
                    tooltip=["policy", alt.Tooltip("wasted_fraction:Q", format=".1%")],
                )
                .properties(height=max(110, 38 * len(waste))),
                use_container_width=True,
            )

    # --- why refused ------------------------------------------------------

    with tabs[2]:
        section("What each policy gave as its reason", "")
        rows = [
            {"policy": r.policy, "reason": reason, "count": count}
            for r in runs
            if r.policy in set(frame["policy"])
            for reason, count in (r.summary.get("reject_reasons") or {}).items()
        ]
        if not rows:
            st.info("Nothing was refused — every request was admitted.")
        else:
            reasons = (
                pd.DataFrame(rows).groupby(["policy", "reason"], as_index=False)["count"].sum()
            )
            st.altair_chart(
                alt.Chart(reasons)
                .mark_bar(height=26)
                .encode(
                    x=alt.X(
                        "count:Q",
                        title="share of refusals",
                        stack="normalize",
                        axis=alt.Axis(format="%"),
                    ),
                    y=alt.Y("policy:N", title=None),
                    color=alt.Color("reason:N", title=None, legend=alt.Legend(orient="top")),
                    tooltip=["policy", "reason", "count"],
                )
                .properties(height=max(120, 42 * reasons["policy"].nunique())),
                use_container_width=True,
            )

        section(
            "What the policy could see", "If the signal never moves, it was reading a flat line."
        )
        pick = {
            f"{r.policy} · repeat {r.repeat}": r for r in runs if r.policy in set(frame["policy"])
        }
        if pick:
            key = st.selectbox("Run", list(pick), label_visibility="collapsed")
            dec = decisions_frame(pick[key])
            if dec.empty:
                st.info("No decision log in this bundle.")
            else:
                signal = st.selectbox(
                    "Signal", ["waiting_requests", "kv_used_fraction", "running_requests"]
                )
                st.altair_chart(
                    alt.Chart(dec)
                    .mark_line(color=YELLOW, strokeWidth=2)
                    .encode(
                        x=alt.X("t:Q", title="seconds into the run"),
                        y=alt.Y(f"{signal}:Q", title=signal),
                    )
                    .properties(height=180)
                    .interactive(),
                    use_container_width=True,
                )
                st.altair_chart(
                    alt.Chart(dec)
                    .mark_circle(size=30, opacity=0.6)
                    .encode(
                        x=alt.X("t:Q", title=None),
                        y=alt.Y("kind:N", title=None, sort=["admit", "defer", "reject"]),
                        color=alt.Color(
                            "kind:N",
                            scale=alt.Scale(
                                domain=list(DECISION_COLORS), range=list(DECISION_COLORS.values())
                            ),
                            legend=alt.Legend(orient="top", title=None),
                        ),
                        tooltip=["request_id", "kind", "reason"],
                    )
                    .properties(height=140)
                    .interactive(),
                    use_container_width=True,
                )
                flat = (
                    signal in dec
                    and dec[signal].notna().any()
                    and float(dec[signal].max()) == float(dec[signal].min())
                )
                if flat:
                    warn(
                        f"<b>{signal} never changed during this run.</b> A policy "
                        "reading it saw a flat line and cannot have been reacting "
                        "to it — which looks identical to deciding everything was "
                        "fine."
                    )

    # --- everything -------------------------------------------------------

    with tabs[3]:
        section("Every run", "Unaggregated — this is where the spread lives.")
        st.dataframe(frame, width="stretch", hide_index=True)
        st.download_button(
            "Download CSV", frame.to_csv(index=False).encode(), f"{chosen}.csv", "text/csv"
        )

        missing = unavailable_metrics(runs)
        if missing:
            section("Not measurable", "Named with the reason rather than estimated.")
            for name, why in missing.items():
                st.markdown(
                    f'<div style="color:{GREY};font-size:0.85rem;margin-bottom:0.3rem">'
                    f"<code>{name}</code> — {why}</div>",
                    unsafe_allow_html=True,
                )

    st.markdown(
        f'<p style="color:{MUTED};font-size:0.78rem;margin-top:2rem">'
        f"Reading <code>{root}</code>. Every bundle records the config and software "
        "versions that produced it.</p>",
        unsafe_allow_html=True,
    )


render()
