"""Terminology, in plain words.

Every term that appears in a chart, a table or a rejection reason. Written for
someone who has not read the code, because that is who needs it — and the
entries that matter most are the pairs that look alike and are not, which is
where most of the confusion comes from.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from components import note, section  # noqa: E402
from theme import GREY, HEADER, LINE, MUTED, SURFACE, WHITE, YELLOW  # noqa: E402

TERMS: list[tuple[str, str, str, str]] = [
    # (term, group, short, why it matters)
    (
        "Admission control",
        "Concepts",
        "Deciding, for each arriving request, whether to accept it, hold it, or "
        "refuse it — before the engine starts work.",
        "Under overload a server that accepts everything serves everyone badly. "
        "Refusing some requests early can mean the rest finish on time.",
    ),
    (
        "Prefill",
        "Concepts",
        "Reading the prompt. Happens once per request, and costs more for longer prompts.",
        "This is what the caller waits through before seeing any output at all.",
    ),
    (
        "Decode",
        "Concepts",
        "Generating the answer, one token at a time, after prefill.",
        "Every token has to arrive promptly or the response looks like it is stuttering.",
    ),
    (
        "KV cache",
        "Concepts",
        "Memory the engine uses to hold what it already computed for each in-flight request.",
        "It is finite. When it fills, the engine must evict or preempt work, and "
        "requests get slower or restart.",
    ),
    (
        "TTFT",
        "What We Measure",
        "Time To First Token — how long the caller waits before seeing anything.",
        "The number admission control affects most, because queueing lands here.",
    ),
    (
        "ITL / TBT",
        "What We Measure",
        "Inter-Token Latency, also called Time Between Tokens — the gap between "
        "consecutive output tokens.",
        "We judge it at each request's <b>p95</b>, not its average. An average "
        "hides stalls: 94 smooth gaps and 6 long ones average to 33ms while "
        "peaking at 400ms, and only one of those numbers describes what the "
        "caller experienced.",
    ),
    (
        "p95",
        "What We Measure",
        "The value 95% of samples fall below. p95 of 200ms means 1 request in 20 waited longer.",
        "Averages hide the bad tail, and the bad tail is what users complain about.",
    ),
    (
        "On time / offered attainment",
        "Scoring",
        "Requests that met their deadline, divided by <b>everything that "
        "arrived</b>. Refused requests count as misses.",
        "The headline. A policy cannot improve this by refusing more — only by "
        "refusing <i>better</i>.",
    ),
    (
        "Of admitted, on time / served attainment",
        "Scoring",
        "Requests that met their deadline, divided by <b>only those let in</b>.",
        "It flatters shedding: refuse 95% of traffic and serve the rest perfectly "
        "and it reads 100%. Never read it without the line above. In one real "
        "run a policy scored 94% here and 40% on offered — worse than doing "
        "nothing.",
    ),
    (
        "Goodput",
        "Scoring",
        "Deadline-meeting requests completed per second.",
        "A rate, not a fraction. The <i>sustainable</i> rate — the highest load "
        "still meeting a target — needs a sweep across several loads, not one "
        "run.",
    ),
    (
        "Wasted work",
        "Scoring",
        "Output tokens generated for requests that missed their deadline anyway.",
        "The clearest statement of what a policy saved by refusing: that compute "
        "bought nobody anything.",
    ),
    (
        "Admit / Defer / Reject",
        "Decisions",
        "The three verdicts a policy can return. Defer means hold briefly and ask again.",
        "Deferring converts a refusal into latency, which is better only if the "
        "caller would rather wait than be told no.",
    ),
    (
        "kv_pressure",
        "Rejection Reasons",
        "Refused because the KV cache was too full.",
        "",
    ),
    (
        "queue_depth",
        "Rejection Reasons",
        "Refused because too many requests were already waiting.",
        "",
    ),
    (
        "deadline_unmeetable",
        "Rejection Reasons",
        "Refused because the predicted wait exceeded what this request was promised.",
        "Only a deadline-aware policy can give this reason.",
    ),
    (
        "overloaded",
        "Rejection Reasons",
        "Refused because the server was judged oversubscribed, before looking at "
        "the individual request.",
        "If most refusals carry this reason, the policy is acting as a blunt rate "
        "limiter rather than reasoning per request.",
    ),
    (
        "no_signal",
        "Rejection Reasons",
        "Refused because the engine's state was too stale to decide on.",
        "The harness, not the policy. Deciding on an old snapshot is guessing.",
    ),
    (
        "Warmup",
        "Running An Experiment",
        "An opening window whose requests are sent but excluded from the results.",
        "They pay for cold caches and one-time setup, which is not the policy's doing.",
    ),
    (
        "Repeats",
        "Running An Experiment",
        "Running the same policy several times.",
        "A live engine gives a different number each time. One run per policy is "
        "a measurement, not a comparison.",
    ),
    (
        "Deployment",
        "Running An Experiment",
        "One provisioned engine: a specific model, on specific hardware, with specific settings.",
        "The unit of comparison. Policies are comparable only within one — a "
        "table mixing two machines reports the machine, not the policy.",
    ),
    (
        "Degraded run",
        "Running An Experiment",
        "A run where most attempts to read engine state failed.",
        "The policy was deciding on stale information, so the numbers describe "
        "the traffic rather than the policy. Hidden by default.",
    ),
    (
        "Concurrency cap (max_num_seqs)",
        "Engine Settings",
        "How many requests the engine will work on at once.",
        "The setting that creates queueing. Raise it too far and nothing queues, "
        "so every policy scores the same and the experiment measures nothing.",
    ),
    (
        "Prefix caching",
        "Engine Settings",
        "Reusing computation when prompts share a beginning.",
        "Turned off for benchmarking: with it on, repeated prompts skip prefill "
        "and pressure stops tracking offered load.",
    ),
]

GROUP_ORDER = [
    "Concepts",
    "What We Measure",
    "Scoring",
    "Decisions",
    "Rejection Reasons",
    "Running An Experiment",
    "Engine Settings",
]


def render() -> None:
    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Terminology")
    note(
        "The two that cause the most confusion are <b>on time</b> and <b>of "
        "admitted, on time</b>. They look interchangeable and are not — one "
        "counts every request that arrived, the other only those let in."
    )

    query = st.text_input("Search", "", placeholder="ttft, goodput, defer…")
    q = query.strip().lower()

    for group in GROUP_ORDER:
        entries = [
            t for t in TERMS if t[1] == group and (not q or q in t[0].lower() or q in t[2].lower())
        ]
        if not entries:
            continue

        section(group, "")
        for term, _, short, why in entries:
            st.markdown(
                f'<div style="background:{SURFACE};border:1px solid {LINE};'
                f"border-left:2px solid {YELLOW};border-radius:4px;"
                f'padding:0.75rem 1rem;margin-bottom:0.5rem">'
                f'<div style="color:{WHITE};font-weight:700;font-size:0.92rem">{term}</div>'
                f'<div style="color:{GREY};font-size:0.86rem;margin-top:0.3rem;'
                f'line-height:1.5">{short}</div>'
                + (
                    f'<div style="color:{MUTED};font-size:0.82rem;margin-top:0.4rem;'
                    f'line-height:1.5"><b>Why it matters.</b> {why}</div>'
                    if why
                    else ""
                )
                + "</div>",
                unsafe_allow_html=True,
            )

    if q and not any(q in t[0].lower() or q in t[2].lower() for t in TERMS):
        st.markdown(
            f'<p style="color:{MUTED};font-size:0.85rem">Nothing matches <code>{query}</code>.</p>',
            unsafe_allow_html=True,
        )


render()
