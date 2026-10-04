"""The dashboard: what a run found, and what a policy bought.

It READS. Nothing here runs a policy, produces a log, or touches a cluster.

Three decisions shape the layout, each taken after the previous version failed on it:

  No sidebar. Two sections do not justify a permanent column, and the charts need the
  width more than the chrome does. Navigation sits in the header; everything that
  selects something sits in one bar beneath it.

  One place per kind of control. "Where am I" and "what am I looking at" were in
  different columns, which is a rule nobody can infer. A control that belongs to one
  chart lives beside that chart, not in a page-level panel.

  Charts are large and interactive. A report already prints the numbers; a page earns
  its place by letting you brush a time range and see the refusals line up with the
  signal crossing its threshold.
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from admitperf.comparison import Comparison
from admitperf.core.log import Log
from admitperf.dashboard import theme
from admitperf.discover import experiments
from admitperf.experiment import Experiment
from admitperf.items import summary as items_summary
from admitperf.policy_runs import PolicyRuns
from admitperf.report import Report
from admitperf.status import Status
from admitperf.terminology import TERMS, groups

ASSETS = Path(__file__).parent / "assets"
#: Comparing is its own section, not a toggle inside Experiments. They answer
#: different questions — "what did this run do" versus "what did the policy buy" — and
#: nesting the second inside the first hid it behind a control nobody had reason to press.
SECTIONS = ("Experiments", "Compare", "Terminology")

#: Chart heights. Named because "tiny charts" was a real complaint and a single place
#: to change them is better than nine call sites.
TALL = 420
MID = 300
VERDICT_COLOUR = {
    "LIVE": theme.YELLOW,
    "INERT": theme.RED,
    "BASELINE": theme.GREY,
    "UNKNOWN": theme.MUTED,
}


def reports(logs: list[Path]) -> list[Report]:
    return [Report.from_log(p) for p in logs]


# --------------------------------------------------------------------------
# Small pieces
# --------------------------------------------------------------------------


def card(key: str, value: str, detail: str = "", colour: str = theme.WHITE) -> str:
    note = f"<div class='d' style='color:{colour}'>{detail}</div>" if detail else ""
    return (
        f"<div class='ap-card'><div class='k'>{key}</div>"
        f"<div class='v' style='color:{colour}'>{value}</div>{note}</div>"
    )


def heading(text: str, note: str = "") -> None:
    extra = f" <span>{note}</span>" if note else ""
    st.markdown(f"<div class='ap-h'>{text}{extra}</div>", unsafe_allow_html=True)


def answer(html: str) -> None:
    st.markdown(f"<div class='ap-answer'>{html}</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Charts — large, interactive, and on the ground rather than a white card
# --------------------------------------------------------------------------


def signal_chart(report: Report, name: str) -> None:
    """The signal over the run, with refusals on the same axis.

    This is the liveness picture, and the one thing text cannot give you: a maximum
    says 0.907 either way, but a line shows whether the signal sat at its ceiling for
    the whole run or touched it once. Putting refusals on the same x-axis then shows
    whether they actually coincide with the crossing — which is the difference between
    a policy that acted and a policy that happened to be running.

    Brushable and zoomable, because the interesting part is usually a window.
    """
    rows = []
    for i, rec in enumerate(report.records):
        value = (rec.get("signals") or {}).get(name)
        if value is None:
            continue
        rows.append(
            {
                "decision": i,
                "value": value,
                "verdict": rec.get("verdict") or "—",
                "reason": rec.get("reason") or "",
            }
        )
    if not rows:
        st.caption(f"`{name}` was never supplied by these metrics.")
        return

    df = pd.DataFrame(rows)
    thr = report.threshold
    hover = alt.selection_point(on="pointerover", nearest=True, fields=["decision"], empty=False)
    zoom = alt.selection_interval(bind="scales", encodings=["x"])

    line = (
        alt.Chart(df)
        .mark_line(color=theme.YELLOW, strokeWidth=1.8, interpolate="monotone")
        .encode(
            x=alt.X("decision:Q", title="decision", scale=alt.Scale(nice=False)),
            y=alt.Y("value:Q", title=name, scale=alt.Scale(zero=True, nice=True)),
        )
    )
    layers = [line]

    if thr is not None:
        # Shade the region the policy can act in, so "above threshold" is an area you
        # can see rather than a number you compare against.
        band = (
            alt.Chart(df)
            .mark_area(color=theme.YELLOW, opacity=0.14, interpolate="monotone")
            .transform_calculate(lo=f"max(datum.value, {thr})")
            .transform_filter(f"datum.value >= {thr}")
            .encode(x="decision:Q", y=alt.datum(thr), y2="value:Q")
        )
        rule = (
            alt.Chart(pd.DataFrame({"y": [thr]}))
            .mark_rule(color=theme.RED, strokeDash=[6, 4], strokeWidth=1.5)
            .encode(y="y:Q")
        )
        layers = [band, line, rule]

    points = (
        alt.Chart(df)
        .mark_circle(size=70)
        .encode(
            x="decision:Q",
            y="value:Q",
            color=alt.Color(
                "verdict:N",
                scale=alt.Scale(
                    domain=["admit", "defer", "reject", "—"],
                    range=[theme.YELLOW, theme.AMBER, theme.RED, theme.MUTED],
                ),
                legend=alt.Legend(title="verdict", orient="top"),
            ),
            opacity=alt.condition(hover, alt.value(1.0), alt.value(0.0)),
            tooltip=[
                alt.Tooltip("decision:Q", title="decision"),
                alt.Tooltip("value:Q", title=name, format=".4g"),
                alt.Tooltip("verdict:N", title="verdict"),
                alt.Tooltip("reason:N", title="reason"),
            ],
        )
        .add_params(hover)
    )

    refusals = df[df["verdict"].isin(["reject", "defer"])]
    if not refusals.empty:
        ticks = (
            alt.Chart(refusals)
            .mark_tick(color=theme.RED, thickness=1.4, size=14, opacity=0.6)
            .encode(x="decision:Q", y=alt.datum(0))
        )
        layers.append(ticks)

    st.altair_chart(
        alt.layer(*layers, points)
        .add_params(zoom)
        .configure(**theme.axis())
        .properties(height=TALL),
        use_container_width=True,
    )
    legend = []
    if thr is not None:
        legend.append(
            f"<span style='color:{theme.RED}'>dashed line</span> is the threshold "
            f"({thr:g}); the shaded band is where the policy can act"
        )
    if not refusals.empty:
        legend.append(f"<span style='color:{theme.RED}'>ticks along the bottom</span> are refusals")
    legend.append("drag to zoom, hover for a decision")
    st.caption(" · ".join(legend), unsafe_allow_html=True)


def ttft_chart(by_policy: dict[str, list[Report]]) -> None:
    """TTFT distribution per policy, overlaid.

    Overlaid rather than one policy at a time: the question is whether the shapes differ,
    and two charts side by side make a reader do that comparison from memory. Served
    requests only — which is why it must be read beside goodput and never alone.
    """
    rows = []
    for name, reps in by_policy.items():
        for r in reps:
            for rec in r.outcomes:
                value = rec["outcome"].get("ttft_ms")
                if value is not None:
                    rows.append({"policy": name, "ttft_ms": value})
    if not rows:
        st.caption("No outcomes recorded, so there is no latency to show.")
        return

    df = pd.DataFrame(rows)
    st.altair_chart(
        alt.Chart(df)
        .transform_density(
            "ttft_ms",
            groupby=["policy"],
            as_=["ttft_ms", "density"],
            extent=[0, float(df.ttft_ms.max())],
        )
        .mark_area(opacity=0.45, interpolate="monotone")
        .encode(
            x=alt.X("ttft_ms:Q", title="TTFT ms, served requests only"),
            y=alt.Y("density:Q", title="density", stack=None),
            color=alt.Color("policy:N", legend=alt.Legend(title=None, orient="top")),
            tooltip=["policy:N", alt.Tooltip("ttft_ms:Q", format=".0f")],
        )
        .interactive()
        .configure(**theme.axis())
        .properties(height=MID),
        use_container_width=True,
    )


def policies_chart(
    by_policy: dict[str, list[Report]], metric, title: str, fmt: str, *, higher_is_better: bool
) -> None:
    """One policy per row: a dot at the median, a line across the observed range.

    A dot plot rather than bars. Bars with a range drawn through them collide, and a
    bar's length implies a magnitude when what matters is the POSITION of two numbers
    relative to each other.

    Colour carries the comparison — grey baseline, green or red by whether a policy beat
    it — so the answer is readable before any number is.
    """
    rows, baseline_value = [], None
    for name, reps in by_policy.items():
        values = [v for r in reps if (v := metric(r)) is not None]
        if not values:
            continue
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        if reps[0].is_baseline:
            baseline_value = median
        rows.append(
            {
                "policy": name,
                "value": median,
                "lo": ordered[0],
                "hi": ordered[-1],
                "baseline": reps[0].is_baseline,
                "runs": len(values),
            }
        )
    if not rows:
        st.caption(f"{title}: not recorded")
        return

    for row in rows:
        if row["baseline"] or baseline_value is None:
            row["verdict"] = "baseline"
        else:
            better = (
                row["value"] > baseline_value if higher_is_better else row["value"] < baseline_value
            )
            row["verdict"] = "better" if better else "worse"

    df = pd.DataFrame(rows)
    shared = alt.Chart(df).encode(
        y=alt.Y("policy:N", title=None, sort=list(df["policy"])),
        color=alt.Color(
            "verdict:N",
            scale=alt.Scale(
                domain=["baseline", "better", "worse"], range=[theme.GREY, theme.GREEN, theme.RED]
            ),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("policy:N", title="policy"),
            alt.Tooltip("value:Q", title="median", format=fmt),
            alt.Tooltip("lo:Q", title="lowest", format=fmt),
            alt.Tooltip("hi:Q", title="highest", format=fmt),
            alt.Tooltip("runs:Q", title="runs"),
        ],
    )
    span = shared.mark_rule(strokeWidth=3, opacity=0.5).encode(x="lo:Q", x2="hi:Q")
    dot = shared.mark_circle(size=320).encode(
        x=alt.X("value:Q", title=title, scale=alt.Scale(zero=False, nice=True))
    )
    label = shared.mark_text(align="left", dx=14, fontSize=13, color=theme.WHITE).encode(
        x="hi:Q", text=alt.Text("value:Q", format=fmt)
    )
    st.altair_chart(
        (span + dot + label).configure(**theme.axis()).properties(height=58 * len(rows) + 50),
        use_container_width=True,
    )


def reasons_chart(by_reason: dict[str, int]) -> None:
    df = pd.DataFrame({"reason": list(by_reason), "requests": list(by_reason.values())})
    st.altair_chart(
        alt.Chart(df)
        .mark_bar(color=theme.YELLOW, height=26)
        .encode(
            x=alt.X("requests:Q", title="requests refused"),
            y=alt.Y("reason:N", title=None, sort="-x"),
            tooltip=["reason:N", "requests:Q"],
        )
        .configure(**theme.axis())
        .properties(height=46 * len(by_reason) + 40),
        use_container_width=True,
    )


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


def report_view(exp: Experiment, runs: PolicyRuns, log: Path) -> None:
    """One run, in the order the survey asks about it.

    Liveness first, because it is the gate: a policy that could not have fired makes
    every number below it a measurement of two identical configurations.
    """
    r = Report.from_log(log)
    label = "BASELINE" if r.is_baseline else r.verdict()
    sig = r.watched_signal()

    if label == "BASELINE":
        gloss = (
            "Admits everything, which is its job — the unmanaged cluster, and what every "
            "every claim is measured against."
        )
    elif sig is None:
        gloss = "No signal was supplied, so whether the policy could have fired is unknown."
    else:
        name, rng, thr = sig
        verb = "reached" if thr and rng["max"] >= thr else "peaked at"
        against = f" against a threshold of <b>{thr:.3g}</b>" if thr else ""
        gloss = f"<code>{name}</code> {verb} <b>{rng['max']:.3g}</b>{against}."
    answer(f"<b style='color:{VERDICT_COLOUR[label]}'>{label}</b> — {gloss}")

    rate = r.offered_rps()
    cols = st.columns(4)
    for col, (k, v, d) in zip(
        cols,
        [
            ("offered", str(len(r.decisions)), f"{rate:.1f}/s" if rate else ""),
            ("admitted", str(len(r.decisions) - r.refused), ""),
            ("refused", str(r.refused), f"{r.refused / max(1, len(r.decisions)):.1%}"),
            ("outcomes", str(len(r.outcomes)), "" if r.outcomes else "none, so no cost figure"),
        ],
        strict=True,
    ):
        col.markdown(card(k, v, d, theme.MUTED), unsafe_allow_html=True)

    # --- signal liveness ------------------------------------------------
    heading("Signal liveness", "reporting item 3 — the column no surveyed paper fills")
    options = [n for n, v in r.signals().items() if v is not None]
    if options:
        watched = sig[0] if sig and sig[0] in options else options[0]
        # The control belongs to this chart, so it sits beside it rather than in a
        # page-level panel.
        chosen = (
            st.segmented_control(
                "Signal", options, default=watched, key="signal", label_visibility="collapsed"
            )
            or watched
        )
        if sig and chosen != sig[0]:
            st.caption(
                f"`{chosen}` is recorded but is **not** what this policy decided on — it "
                f"read `{sig[0]}`. Every signal is logged so you can see whether a "
                "different one would have fired."
            )
        signal_chart(r, chosen)
    else:
        st.caption("No signal was supplied by these metrics.")

    # --- what it refused ------------------------------------------------
    if by_reason := r.by_reason():
        heading("What it refused, and why")
        reasons_chart(by_reason)
        st.caption(
            "Reasons come from a controlled vocabulary, so these group with another "
            "deployment's. Free text cannot be grouped at all."
        )

    # --- what it cost ---------------------------------------------------
    heading("What it cost", "served requests only, so read it beside goodput")
    ttft_chart({runs.label: [r]})
    st.code("\n".join(r.cost_text()), language=None)

    # --- the seven reporting items --------------------------------------
    items = r.items(repeats=len(runs.logs), has_baseline=exp.baseline is not None)
    heading("The survey's seven reporting items", items_summary(items))
    st.caption(
        "These record where the sixteen surveyed papers diverge. *not recorded* means "
        "this log does not carry the fact, which is neither a pass nor a failure."
    )
    for item in items:
        mark, colour = {
            Status.OK: ("✓", theme.GREEN),
            Status.FAIL: ("✕", theme.RED),
            Status.UNEVIDENCED: ("—", theme.MUTED),
        }[item.status]
        with st.expander(f"{mark}  {item.number}. {item.name}"):
            st.markdown(
                f"<span style='color:{colour}'>{item.detail}</span>", unsafe_allow_html=True
            )
            st.caption(f"The survey asks for: {item.asks}")

    # --- the policy card ------------------------------------------------
    heading("Where this policy sits in the survey's table")
    st.dataframe(
        [
            {"axis": k.replace("_", " "), "value": v if v is not None else "—"}
            for k, v in r.card.items()
        ],
        use_container_width=True,
        hide_index=True,
    )
    if r.card.get("signal_structure") == "dual-gate":
        st.warning(
            "**dual-gate** — this policy ANDs two signals, so one signal's range does "
            "not establish liveness for it. It can hold its first signal above threshold "
            "for an entire run and never fire."
        )

    with st.expander("The full text report"):
        st.code(r.text(), language=None)
    with st.expander(f"Raw log — {len(Log.read(log))} records"):
        st.json(Log.read(log)[:20])


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


def _delta(before: float | None, after: float | None, *, lower_is_better: bool) -> tuple[str, str]:
    if before is None or after is None or not before:
        return "", theme.MUTED
    better = (after < before) if lower_is_better else (after > before)
    colour = theme.GREEN if better else theme.RED
    arrow = "▼" if after < before else "▲"
    fmt = "{:.3f}" if abs(before) < 10 else "{:.0f}"
    suffix = "" if better else " — worse"
    return f"{arrow} from {fmt.format(before)}{suffix}", colour


def comparison_view(exp: Experiment, candidate: PolicyRuns) -> None:
    base, pol = reports(exp.baseline.logs), reports(candidate.logs)
    c = Comparison(base, pol)

    p95 = lambda r: t["p95"] if (t := c._ttft(r)) else None  # noqa: E731
    bt, pt = c._arm(base, p95), c._arm(pol, p95)
    bg, pg = c._arm(base, c._goodput), c._arm(pol, c._goodput)

    if not c.fired():
        answer(
            "<b>No finding.</b> The policy refused nothing, so these two runs are the "
            "same configuration measured twice — any difference below is noise."
        )
    elif not (bt and pt and bg and pg):
        answer(
            f"{c.policy.policy} refused <b>{c.refused_share():.1%}</b>. What that bought "
            "cannot be measured: no outcomes were recorded."
        )
    else:
        factor = bt[0] / pt[0] if pt[0] else float("inf")
        worse = pg[0] < bg[0]
        verdict = (
            "but <b>goodput fell</b>, so it refused requests the cluster could have served"
            if worse
            else "and <b>goodput rose</b>"
        )
        answer(
            f"{c.policy.policy} refused <b>{c.refused_share():.1%}</b>, cut p95 TTFT "
            f"<b>{factor:.2f}×</b>, {verdict} ({bg[0]:.3f} → {pg[0]:.3f})."
        )

    cols = st.columns(3)
    d1, c1 = _delta(bt and bt[0], pt and pt[0], lower_is_better=True)
    d2, c2 = _delta(bg and bg[0], pg and pg[0], lower_is_better=False)
    cols[0].markdown(
        card("p95 TTFT of served", f"{pt[0]:.0f}ms" if pt else "—", d1, c1), unsafe_allow_html=True
    )
    cols[1].markdown(
        card("goodput, of offered", f"{pg[0]:.3f}" if pg else "—", d2, c2), unsafe_allow_html=True
    )
    cols[2].markdown(
        card("refused", f"{c.refused_share():.1%}", "of everything offered", theme.YELLOW),
        unsafe_allow_html=True,
    )

    all_runs = {p.label: reports(p.logs) for p in exp.policies.values()}

    heading("Every policy, with the spread across runs")
    left, right = st.columns(2)
    with left:
        policies_chart(
            all_runs,
            c._goodput,
            "goodput, of offered — higher is better",
            ".3f",
            higher_is_better=True,
        )
    with right:
        policies_chart(
            all_runs, p95, "p95 TTFT ms — lower is better", ".0f", higher_is_better=False
        )
    st.caption(
        f"Dot is the median; the line through it is the range across repeats. "
        f"<span style='color:{theme.GREY}'>grey = baseline</span> · "
        f"<span style='color:{theme.GREEN}'>green = beat it</span> · "
        f"<span style='color:{theme.RED}'>red = worse</span>. Two arms whose lines "
        "overlap have not separated, whatever the gap between their dots looks like.",
        unsafe_allow_html=True,
    )

    heading("Latency distribution, every policy overlaid")
    ttft_chart(all_runs)
    st.caption(
        "Served requests only, so a shedding policy flatters itself here — which is "
        "exactly why it sits beside goodput."
    )

    heading("Can you trust it?")
    for label, ok in [
        ("the policy fired in every repeat", c.fired()),
        ("every run faced the same load", c.comparable_load()),
        ("outcomes recorded, so cost is measurable", c.measurable()),
        (f"the two separate across {c.repeats} run(s)", c.separated()),
    ]:
        mark, colour = (
            ("—", theme.MUTED) if ok is None else (("✓", theme.GREEN) if ok else ("✕", theme.RED))
        )
        st.markdown(
            f"<div class='ap-check'><span style='color:{colour};font-weight:700'>{mark}</span>"
            f"&nbsp;&nbsp;{label}</div>",
            unsafe_allow_html=True,
        )
    if c.separated() is None:
        st.caption("One run per policy, so there is no error bar: `admitperf demo --repeats 5`.")

    with st.expander("The full text comparison"):
        st.code(c.text(), language=None)


# --------------------------------------------------------------------------
# Terminology
# --------------------------------------------------------------------------


def terminology_view() -> None:
    """Every term a report can print.

    Here because two pairs cause nearly all the confusion in this area — *offered*
    versus *admitted* as a denominator, and *inert* versus *unknown* — and in both
    cases the two members look interchangeable and are not.
    """
    st.markdown("#### Terminology")
    st.caption(
        "Definitions marked **survey** use the survey's own wording, so this explains "
        "the concept rather than paraphrasing it into something subtly different."
    )
    all_groups = groups()
    chosen = (
        st.segmented_control(
            "Group",
            ["All", *all_groups],
            default="All",
            key="term_group",
            label_visibility="collapsed",
        )
        or "All"
    )
    shown = all_groups if chosen == "All" else {chosen: all_groups[chosen]}

    for group, terms in shown.items():
        heading(group)
        # Three across, so thirty definitions are a page rather than a scroll.
        for row_start in range(0, len(terms), 3):
            for col, term in zip(st.columns(3), terms[row_start : row_start + 3], strict=False):
                gloss, why, source = TERMS[term]
                tag = (
                    f"<span style='color:{theme.YELLOW};font-size:0.62rem;"
                    "text-transform:uppercase;letter-spacing:0.09em'>&nbsp;survey</span>"
                    if source == "survey"
                    else ""
                )
                col.markdown(
                    f"<div class='ap-card' style='margin-bottom:0.7rem'>"
                    f"<div class='k'>{term}{tag}</div>"
                    f"<div style='color:{theme.WHITE};font-size:0.92rem;margin-top:0.3rem'>{gloss}</div>"
                    f"<div style='color:{theme.GREY};font-size:0.82rem;margin-top:0.4rem'>{why}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )


# --------------------------------------------------------------------------


def sidebar(found: dict[str, Experiment] | None) -> tuple[str, Experiment | None]:
    """Wordmark, navigation, and the experiment — everything that scopes the page.

    The wordmark is markup we emit rather than `st.logo`, and the nav is buttons rather
    than `st.navigation`, for the same reason: both of Streamlit's built-ins render into
    a slot ABOVE anything written to the sidebar body, so the wordmark could never be
    first. Owning them makes the order on screen the order these lines run in.
    """
    st.sidebar.markdown(
        f"<div class='ap-mark-side'><span style='color:{theme.YELLOW}'>Admit</span>"
        f"<span style='color:{theme.WHITE}'>Perf</span></div>",
        unsafe_allow_html=True,
    )

    if "section" not in st.session_state:
        st.session_state.section = SECTIONS[0]
    for label in SECTIONS:
        active = st.session_state.section == label
        if st.sidebar.button(
            label, key=f"nav_{label}", width="stretch", type="primary" if active else "secondary"
        ):
            st.session_state.section = label
            st.rerun()
    section = st.session_state.section

    exp = None
    if section in ("Experiments", "Compare") and found:
        st.sidebar.divider()
        if len(found) == 1:
            # A dropdown offering one option is chrome pretending to be a choice.
            exp = next(iter(found.values()))
            st.sidebar.markdown(
                f"<div style='color:{theme.MUTED};font-size:0.68rem;"
                f"text-transform:uppercase;letter-spacing:0.09em'>experiment</div>"
                f"<div style='color:{theme.WHITE};font-size:0.95rem;font-weight:600'>"
                f"{exp.name}</div>",
                unsafe_allow_html=True,
            )
        else:
            exp = found[st.sidebar.selectbox("Experiment", sorted(found), key="experiment")]
        st.sidebar.caption(f"{len(exp.policies)} policies · {exp.runs} runs")
        if exp.notes:
            # What the name cannot say: the hardware, the workload, the regime.
            st.sidebar.caption(exp.notes)
    return section, exp


def run_controls(exp: Experiment) -> tuple[PolicyRuns, Path]:
    """Which policy, and which of its runs. Inline above the content.

    Inline rather than in a right-hand panel: that panel plus the sidebar left the
    charts about sixty percent of the width, which for a page whose job is a chart is
    the wrong way round.
    """
    st.markdown("<div class='ap-bar'>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3, 2, 3])

    # Options are label strings, not objects: `experiments()` rebuilds them on every
    # rerun, so a stored selection could not be matched against the new list and the
    # content rendered one interaction behind the selector.
    by_label = {p.label: p for p in exp.policies.values()}
    chosen = by_label[c1.selectbox("Policy", list(by_label), key="run_policy")]

    runs = {p.stem: p for p in chosen.logs}
    log = chosen.logs[0] if len(runs) == 1 else runs[c2.selectbox("Run", list(runs), key="run")]
    c3.markdown(
        f"<div style='padding-top:1.55rem;color:{theme.MUTED};font-size:0.82rem'>"
        f"{len(runs)} run(s) of this policy"
        + (" — one run has no error bar" if len(runs) == 1 else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)
    return chosen, log


def compare_controls(exp: Experiment) -> PolicyRuns | None:
    """Which policy to hold against the baseline.

    The baseline is not a choice: an experiment has one policy that admits everything, and
    comparing two policies against each other answers a different question from the one
    this section asks.
    """
    if exp.baseline is None:
        st.warning(
            f"**{exp.name}** has no baseline, so there is nothing to compare "
            'against — and "the policy refused 27%" has nothing to be 27% of. Run an '
            "arm whose policy admits everything; `NoAdmission` is one."
        )
        return None
    if not exp.candidates:
        st.info(
            f"**{exp.name}** has only a baseline. Run a policy under the same "
            "experiment name and it will appear here."
        )
        return None

    st.markdown("<div class='ap-bar'>", unsafe_allow_html=True)
    c1, c2 = st.columns([3, 5])
    c1.markdown(
        f"<div style='padding-top:0.3rem'><div style='color:{theme.MUTED};"
        f"font-size:0.68rem;text-transform:uppercase;letter-spacing:0.09em'>baseline</div>"
        f"<div style='color:{theme.GREY};font-size:0.95rem'>{exp.baseline.label}</div></div>",
        unsafe_allow_html=True,
    )
    by_label = {p.label: p for p in exp.candidates}
    candidate = by_label[c2.selectbox("Compared with", list(by_label), key="cmp_policy")]
    st.markdown("</div>", unsafe_allow_html=True)
    return candidate


def main() -> None:
    st.set_page_config(
        page_title="AdmitPerf",
        page_icon=str(ASSETS / "icon.png"),
        layout="wide",
    )
    st.markdown(theme.CSS, unsafe_allow_html=True)

    found = experiments(Path.cwd())
    section, exp = sidebar(found)

    if section == "Terminology":
        terminology_view()
        return

    if not found or exp is None:
        st.info(
            "No AdmitPerf logs under this directory. Make some:\n\n"
            "```\nadmitperf demo --repeats 5\n```\n\n"
            "or record a real cluster:\n\n"
            "```\nadmitperf watch http://localhost:8000/metrics --for 5m -o trace.jsonl\n```"
        )
        return

    st.markdown(f"#### {exp.name}")
    if section == "Compare":
        candidate = compare_controls(exp)
        if candidate is not None:
            comparison_view(exp, candidate)
    else:
        chosen, log = run_controls(exp)
        report_view(exp, chosen, log)


main()
