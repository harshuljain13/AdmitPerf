"""AdmitPerf results dashboard.

    streamlit run dashboard/app.py -- --results results/

Reads the bundles `bench run` writes. Nothing here needs a GPU or the harness
running, so results can be copied off a machine that has since been torn down.

Two rules shape the whole layout, both carried over from the CLI because they
are what keep a comparison honest:

- **A deployment is the unit of comparison.** Policies are comparable only when
  they faced the same engine on the same hardware, so the app groups by
  deployment and never pools across.
- **Offered attainment is the headline, served is shown beside it.** Served
  alone flatters shedding: refuse 95% and serve the rest perfectly and it reads
  1.00.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from theme import CSS, DECISION_COLORS, SLATE, TEAL, policy_color_map  # noqa: E402

from data import (  # noqa: E402
    decisions_frame,
    discover,
    runs_frame,
    summarise,
    unavailable_metrics,
)

st.set_page_config(page_title="AdmitPerf", page_icon="🚦", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)


def _results_root() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results")
    known, _ = parser.parse_known_args()
    return Path(known.results)


@st.cache_data(show_spinner=False)
def _load(root: str, _mtime: float) -> tuple[pd.DataFrame, dict[str, str], list[str]]:
    runs = discover(Path(root))
    return runs_frame(runs), unavailable_metrics(runs), [str(r.path) for r in runs]


def _newest_mtime(root: Path) -> float:
    """Cache key: reload when any bundle changes, so a run finishing mid-session
    shows up without restarting the app."""
    times = [p.stat().st_mtime for p in root.rglob("summary.json")]
    return max(times) if times else 0.0


# --- header ---------------------------------------------------------------

st.markdown(
    """
    <div class="ap-banner">
      <h1>🚦 AdmitPerf</h1>
      <p>Admission control for LLM inference — which policy wins, on what, and by how much.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

root = _results_root()
if not root.exists():
    st.error(f"No results directory at `{root}`.")
    st.markdown(
        "Run an experiment first, or point the app elsewhere:\n\n"
        "```bash\n"
        "admitperf bench run -c experiments/demo.yaml\n"
        "streamlit run dashboard/app.py -- --results /path/to/results\n"
        "```"
    )
    st.stop()

frame, unavailable, _paths = _load(str(root), _newest_mtime(root))

if frame.empty:
    st.warning(f"No run bundles found under `{root}`.")
    st.stop()

# --- filters --------------------------------------------------------------

with st.sidebar:
    st.header("Filter")

    experiments = sorted(frame["experiment"].dropna().unique())
    chosen_exp = st.multiselect("Experiment", experiments, default=experiments)
    frame = frame[frame["experiment"].isin(chosen_exp)]

    deployments = sorted(frame["deployment"].dropna().unique())
    # One deployment at a time by default. Selecting several puts
    # non-comparable rows on one chart, so the app warns when you do.
    chosen_dep = st.multiselect(
        "Deployment",
        deployments,
        default=deployments[:1] if len(deployments) > 1 else deployments,
        help="Policies are only comparable within a deployment — same engine, "
        "same hardware. Selecting more than one is allowed, but the charts "
        "will say so.",
    )
    frame = frame[frame["deployment"].isin(chosen_dep)]

    policies = sorted(frame["policy"].dropna().unique())
    chosen_pol = st.multiselect("Policy", policies, default=policies)
    frame = frame[frame["policy"].isin(chosen_pol)]

    hide_degraded = st.checkbox(
        "Hide degraded runs",
        value=True,
        help="Runs where most engine scrapes failed. The policy decided on "
        "stale state, so the numbers describe the workload rather than the "
        "policy.",
    )
    degraded = int((~frame["healthy"]).sum())
    if hide_degraded:
        frame = frame[frame["healthy"]]

    st.divider()
    st.caption(f"{len(frame)} runs · {frame['policy'].nunique()} policies")

if frame.empty:
    st.warning("Nothing selected.")
    st.stop()

if degraded and not hide_degraded:
    st.markdown(
        f'<div class="ap-warn"><b>{degraded} degraded run(s) included.</b> Most '
        "engine scrapes failed in these, so the policy was deciding on stale "
        "state and the numbers do not describe it.</div>",
        unsafe_allow_html=True,
    )

if len(chosen_dep) > 1:
    st.markdown(
        '<div class="ap-warn"><b>Multiple deployments selected.</b> Rows below '
        "span different hardware or engine settings. Differences between them "
        "may be the machine rather than the policy.</div>",
        unsafe_allow_html=True,
    )

colors = policy_color_map(sorted(frame["policy"].unique()))
agg = summarise(frame)

# --- headline -------------------------------------------------------------

best = agg.loc[agg["offered_attainment"].idxmax()] if not agg.empty else None
baseline_row = agg[agg["policy"] == "no_admission"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Runs", len(frame))
c2.metric("Policies", frame["policy"].nunique())
if best is not None:
    c3.metric(
        "Best offered attainment",
        f"{best['offered_attainment']:.3f}",
        help="Met SLO ÷ everything that arrived. Rejections count as misses, "
        "so a policy cannot improve this by refusing more — only by refusing "
        "better.",
    )
    delta = None
    if not baseline_row.empty:
        base = float(baseline_row.iloc[0]["offered_attainment"])
        delta = f"{(best['offered_attainment'] - base):+.3f} vs no_admission"
    c4.metric("Winner", str(best["policy"]), delta=delta)

st.markdown(
    '<div class="ap-note"><b>Offered</b> = met ÷ arrived, counting rejections as '
    "misses — the headline. <b>Served</b> = met ÷ admitted, which flatters "
    "shedding and is never shown alone. <b>req/s</b> is SLO-meeting requests per "
    "second of wall clock.</div>",
    unsafe_allow_html=True,
)
st.write("")

tab_compare, tab_tradeoff, tab_timeline, tab_detail, tab_data = st.tabs(
    ["Comparison", "The trade-off", "Over time", "Why refused", "Data"]
)

# --- comparison -----------------------------------------------------------

with tab_compare:
    st.subheader("Attainment by policy")

    long = agg.melt(
        id_vars=["deployment", "policy"],
        value_vars=["offered_attainment", "served_attainment"],
        var_name="measure",
        value_name="value",
    ).dropna(subset=["value"])
    long["measure"] = long["measure"].map(
        {"offered_attainment": "offered (headline)", "served_attainment": "served"}
    )

    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("value:Q", title="SLO attainment", scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("policy:N", title=None, sort="-x"),
            color=alt.Color(
                "measure:N",
                title=None,
                scale=alt.Scale(range=[TEAL, SLATE]),
                legend=alt.Legend(orient="top"),
            ),
            yOffset="measure:N",
            row=alt.Row("deployment:N", title=None) if len(chosen_dep) > 1 else alt.Row(),
            tooltip=["policy", "measure", alt.Tooltip("value:Q", format=".3f")],
        )
        .properties(height=max(160, 46 * agg["policy"].nunique()))
    )
    st.altair_chart(chart, width="stretch")

    st.caption(
        "A large gap between the two bars means the policy is refusing heavily. "
        "Whether that was worthwhile is the offered bar, not the served one."
    )

    st.subheader("Summary")
    show = agg.copy()
    show["TTFT p95"] = show.apply(
        lambda r: (
            (
                f"{r['ttft_p95']:.0f}ms"
                + (f" ±{r['ttft_p95_spread']:.0f}" if pd.notna(r.get("ttft_p95_spread")) else "")
            )
            if pd.notna(r["ttft_p95"])
            else "—"
        ),
        axis=1,
    )
    cols = {
        "deployment": "deployment",
        "policy": "policy",
        "runs": "runs",
        "admit_rate": "admit rate",
        "offered_attainment": "offered",
        "served_attainment": "served",
        "goodput_rps": "req/s",
        "TTFT p95": "TTFT p95",
        "wasted_fraction": "wasted tokens",
    }
    table = show[list(cols)].rename(columns=cols)
    for pct in ("admit rate", "wasted tokens"):
        table[pct] = table[pct] * 100.0
    # A column chart rather than a colour gradient: background_gradient pulls
    # in matplotlib, which is a large dependency for one shading effect.
    st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        column_config={
            "offered": st.column_config.ProgressColumn(
                "offered",
                help="Met SLO / everything that arrived. The headline.",
                format="%.3f",
                min_value=0.0,
                max_value=1.0,
            ),
            "served": st.column_config.NumberColumn(
                "served",
                help="Met SLO / admitted. Flatters shedding — read it beside offered.",
                format="%.3f",
            ),
            "admit rate": st.column_config.NumberColumn("admit rate", format="%.1f%%"),
            "req/s": st.column_config.NumberColumn("req/s", format="%.2f"),
            "wasted tokens": st.column_config.NumberColumn("wasted tokens", format="%.1f%%"),
        },
    )
    single = agg[agg["runs"] < 2]["policy"].tolist()
    if single:
        st.caption(
            f"⚠️ Only one run for {', '.join(single)} — no spread to report. "
            "Use `--repeats 3` before trusting a difference."
        )

# --- trade-off ------------------------------------------------------------

with tab_tradeoff:
    st.subheader("What refusing buys, and what it costs")
    st.caption(
        "Up and to the left is better: low tail latency without giving up "
        "attainment. A policy far left but low is refusing traffic it could "
        "have served."
    )

    points = frame.dropna(subset=["ttft_p95", "offered_attainment"])
    if points.empty:
        st.info("No runs with both latency and attainment recorded.")
    else:
        scatter = (
            alt.Chart(points)
            .mark_circle(size=170, opacity=0.8)
            .encode(
                x=alt.X("ttft_p95:Q", title="TTFT p95 (ms) — lower is better"),
                y=alt.Y("offered_attainment:Q", title="Offered attainment — higher is better"),
                color=alt.Color(
                    "policy:N",
                    scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                    legend=alt.Legend(orient="bottom", columns=3),
                ),
                size=alt.Size("admit_rate:Q", title="admit rate", scale=alt.Scale(range=[60, 420])),
                tooltip=[
                    "policy",
                    "deployment",
                    "repeat",
                    alt.Tooltip("ttft_p95:Q", title="TTFT p95", format=".0f"),
                    alt.Tooltip("offered_attainment:Q", title="offered", format=".3f"),
                    alt.Tooltip("served_attainment:Q", title="served", format=".3f"),
                    alt.Tooltip("admit_rate:Q", title="admit rate", format=".1%"),
                ],
            )
            .properties(height=420)
            .interactive()
        )
        st.altair_chart(scatter, width="stretch")

    st.subheader("Wasted work")
    st.caption(
        "Tokens generated for requests that missed their deadline anyway. This "
        "is the clearest statement of what a policy saved by refusing."
    )
    waste = agg.dropna(subset=["wasted_fraction"])
    if not waste.empty:
        st.altair_chart(
            alt.Chart(waste)
            .mark_bar()
            .encode(
                x=alt.X(
                    "wasted_fraction:Q",
                    title="share of output tokens wasted",
                    axis=alt.Axis(format="%"),
                ),
                y=alt.Y("policy:N", sort="-x", title=None),
                color=alt.Color(
                    "policy:N",
                    scale=alt.Scale(domain=list(colors), range=list(colors.values())),
                    legend=None,
                ),
                tooltip=["policy", alt.Tooltip("wasted_fraction:Q", format=".1%")],
            )
            .properties(height=max(140, 40 * waste["policy"].nunique())),
            width="stretch",
        )

# --- timeline -------------------------------------------------------------

with tab_timeline:
    st.subheader("Pressure and decisions over one run")
    st.caption(
        "The state a policy was reading, and what it did about it. If the "
        "signal never moves, the policy was reading a flat line — which looks "
        "identical to a policy that decided everything was fine."
    )

    runs = discover(root)
    picked = {f"{r.deployment} · {r.policy} · repeat {r.repeat}": r for r in runs}
    keys = [k for k in picked if picked[k].policy in chosen_pol]
    if not keys:
        st.info("No runs match the current filter.")
    else:
        key = st.selectbox("Run", keys)
        run = picked[key]
        dec = decisions_frame(run)

        if dec.empty:
            st.info("No decision log in this bundle.")
        else:
            left, right = st.columns(2)
            signal = left.selectbox(
                "Signal",
                ["waiting_requests", "kv_used_fraction", "running_requests", "state_age_s"],
                help="What the policy could see when it decided.",
            )
            smooth = right.slider("Smoothing (rolling window)", 1, 50, 1)

            series = dec[["t", signal]].dropna()
            if smooth > 1:
                series[signal] = series[signal].rolling(smooth, min_periods=1).mean()

            st.altair_chart(
                alt.Chart(series)
                .mark_line(color=TEAL, strokeWidth=2)
                .encode(
                    x=alt.X("t:Q", title="seconds into the run"),
                    y=alt.Y(f"{signal}:Q", title=signal),
                    tooltip=[alt.Tooltip("t:Q", format=".1f"), signal],
                )
                .properties(height=220)
                .interactive(),
                width="stretch",
            )

            st.altair_chart(
                alt.Chart(dec)
                .mark_circle(size=34, opacity=0.55)
                .encode(
                    x=alt.X("t:Q", title="seconds into the run"),
                    y=alt.Y("kind:N", title=None, sort=["admit", "defer", "reject"]),
                    color=alt.Color(
                        "kind:N",
                        scale=alt.Scale(
                            domain=list(DECISION_COLORS), range=list(DECISION_COLORS.values())
                        ),
                        legend=alt.Legend(orient="top", title=None),
                    ),
                    tooltip=["request_id", "kind", "reason", signal],
                )
                .properties(height=170)
                .interactive(),
                width="stretch",
            )

            if signal in dec and dec[signal].notna().any():
                spread = float(dec[signal].max()) - float(dec[signal].min())
                if spread == 0:
                    st.markdown(
                        f'<div class="ap-warn"><b>{signal} never changed.</b> A policy '
                        "reading this signal saw a flat line and could not have been "
                        "reacting to it.</div>",
                        unsafe_allow_html=True,
                    )

# --- why refused ----------------------------------------------------------

with tab_detail:
    st.subheader("Why requests were refused")
    st.caption(
        "Reasons come from the policy itself, so a refusal is explainable "
        "afterwards rather than merely counted."
    )

    runs = discover(root)
    wanted = [r for r in runs if r.policy in chosen_pol and r.deployment in chosen_dep]
    rows = []
    for r in wanted:
        for reason, count in (r.summary.get("reject_reasons") or {}).items():
            rows.append({"policy": r.policy, "reason": reason, "count": count})

    if not rows:
        st.info("No refusals in the selected runs — every request was admitted.")
    else:
        reasons = pd.DataFrame(rows).groupby(["policy", "reason"], as_index=False)["count"].sum()
        st.altair_chart(
            alt.Chart(reasons)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="requests refused", stack="normalize"),
                y=alt.Y("policy:N", title=None),
                color=alt.Color("reason:N", title="reason", legend=alt.Legend(orient="top")),
                tooltip=["policy", "reason", "count"],
            )
            .properties(height=max(140, 44 * reasons["policy"].nunique())),
            width="stretch",
        )
        st.dataframe(
            reasons.pivot(index="policy", columns="reason", values="count").fillna(0).astype(int),
            width="stretch",
        )

    st.subheader("Which promise was missed")
    detail = (
        frame.groupby("policy", as_index=False)[["missed_ttft", "missed_itl", "failed_outright"]]
        .sum()
        .melt(id_vars="policy", var_name="kind", value_name="count")
    )
    detail = detail[detail["count"] > 0]
    if detail.empty:
        st.info("Nothing missed a deadline in the selected runs.")
    else:
        detail["kind"] = detail["kind"].map(
            {
                "missed_ttft": "first token too slow",
                "missed_itl": "stream too choppy",
                "failed_outright": "request failed",
            }
        )
        st.altair_chart(
            alt.Chart(detail)
            .mark_bar()
            .encode(
                x=alt.X("count:Q", title="requests"),
                y=alt.Y("policy:N", title=None),
                color=alt.Color("kind:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=["policy", "kind", "count"],
            )
            .properties(height=max(140, 44 * detail["policy"].nunique())),
            width="stretch",
        )

# --- data -----------------------------------------------------------------

with tab_data:
    st.subheader("Every run")
    st.caption("One row per run, not per policy — this is where the spread lives.")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "Download as CSV",
        frame.to_csv(index=False).encode(),
        file_name="admitperf-runs.csv",
        mime="text/csv",
    )

    if unavailable:
        st.subheader("Not measurable")
        st.caption(
            "Named with the reason rather than estimated, so a reader can tell "
            "a metric that is zero from one that was never obtainable."
        )
        for name, why in unavailable.items():
            st.markdown(f"- **`{name}`** — {why}")

st.divider()
st.markdown(
    f'<p style="color:{SLATE};font-size:0.82rem">'
    f"Reading <code>{root}</code>. Every bundle records the resolved config that "
    "produced it, so a number can be traced back to the engine settings behind it."
    "</p>",
    unsafe_allow_html=True,
)
