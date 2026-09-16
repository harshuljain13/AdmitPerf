"""The policies, explained.

Reads the live registry so the list cannot drift from what is installed, and
pairs each entry with prose about *when it fits and when it misleads* — which
is the part a signature cannot tell you.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from components import note, section, warn  # noqa: E402
from theme import GREY, HEADER, LINE, MUTED, SURFACE, WHITE, YELLOW  # noqa: E402

from admitperf.core.registry import available  # noqa: E402

#: Curated notes per policy. Deliberately about judgement rather than
#: mechanics: the code says what a policy does, this says when to believe it.
NOTES: dict[str, dict[str, str]] = {
    "no_admission": {
        "one_line": "Accepts everything. The thing every other policy has to beat.",
        "how": "No decision at all — every request goes straight to the engine. "
        "This is what vLLM does out of the box.",
        "fits": "Always include it. Without a baseline there is no way to say how "
        "much of a result is the policy and how much is the workload.",
        "fails": "Under overload it admits requests that cannot possibly finish "
        "in time, which delays everything queued behind them.",
    },
    "queue_depth": {
        "one_line": "Refuses when too many requests are already waiting.",
        "how": "Reads the engine's own queue depth and refuses past a threshold. "
        "Four lines of logic.",
        "fits": "When the concurrency cap binds first — a small model where the "
        "engine's sequence limit is reached long before memory is.",
        "fails": "It knows nothing about what any individual request asked for, "
        "so it treats an interactive request and an overnight batch job "
        "identically. It cannot prefer the ones it could still serve on time.",
    },
    "queue_depth_defer": {
        "one_line": "Same rule, but holds the request instead of refusing it.",
        "how": "Waits a short interval and asks again, so the policy sees fresher "
        "state on the second look.",
        "fits": "When your callers would rather wait than be told no.",
        "fails": "Converts a refusal into latency. If the queue never drains, the "
        "request is eventually refused anyway — having already spent the wait.",
    },
    "kv_threshold": {
        "one_line": "Refuses when the KV cache is nearly full.",
        "how": "Reads KV cache utilisation from the engine and refuses above a fraction.",
        "fits": "Long contexts and large batches, where memory genuinely fills "
        "before anything else runs out.",
        "fails": "On a small model the cache is far larger than a handful of short "
        "sequences can fill. We measured it never exceeding 0.005 while the queue "
        "was 24 deep — the policy read a flat line near zero and silently behaved "
        "as accept-everything, scoring identically to the baseline while appearing "
        "to work.",
    },
    "chronos_inspired": {
        "one_line": "Refuses requests it can show will miss their deadline.",
        "how": "Three checks in order: is the server oversubscribed; would this "
        "request's predicted wait exceed its own deadline; would one more response "
        "break the token rhythm. The rejection reason records which one fired.",
        "fits": "Mixed traffic where requests promise different things. It is the "
        "only policy here that reads the request rather than only the server, so "
        "two requests arriving into identical conditions can get different answers.",
        "fails": "It depends on a cost model of the hardware. One parameter cannot "
        "be measured from engine telemetry, so on hardware unlike the paper's it "
        "over-estimates cost, over-estimates load, and degenerates into a blunt "
        "rate limiter — which is exactly the baseline the paper argues against.",
        "source": "Marref, Tarmissi & Chaibi, *Frontiers in Computer Science* 8 "
        "(2026). doi.org/10.3389/fcomp.2026.1873627",
        "fidelity": "Named *inspired* because the algorithm is the paper's but the "
        "parameters are fitted from a live engine rather than derived from a "
        "roofline model. It runs their test; it does not reproduce their guarantee.",
    },
}

SIGNALS = {
    "waiting_requests": "how many requests the engine has queued",
    "running_requests": "how many are executing right now",
    "kv_used_fraction": "how full the KV cache is, 0 to 1",
}


def render() -> None:
    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Algorithms")
    note(
        "A policy reading the wrong signal for its situation <b>does not fail "
        "loudly</b>. It sees a flat line and quietly becomes accept-everything, "
        "scoring like the baseline while appearing to work. Which signal carries "
        "the pressure is the first thing to get right."
    )

    registry = available()

    section("Choosing one", "")
    st.markdown(
        """
| If the bottleneck is… | Use | Because |
|---|---|---|
| the concurrency cap | `queue_depth` | queue depth is what moves |
| KV memory | `kv_threshold` | cache pressure is what moves |
| meeting per-request deadlines | `chronos_inspired` | it is the only one that reads the deadline |
| nothing — you need a baseline | `no_admission` | it is the thing to beat |
"""
    )

    section("Every installed policy", f"{len(registry)} found in the registry.")

    for name in sorted(registry):
        cls = registry[name]
        info = NOTES.get(name, {})
        requires = sorted(getattr(cls, "requires", frozenset()))

        with st.expander(
            f"**{name}** — {info.get('one_line', 'no description available')}",
            expanded=False,
        ):
            if info.get("how"):
                st.markdown(f"**How it decides.** {info['how']}")

            if requires:
                chips = "  ".join(
                    f'<code style="background:{SURFACE};border:1px solid {LINE};'
                    f"color:{YELLOW};padding:0.1rem 0.4rem;border-radius:3px;"
                    f'font-size:0.8rem">{s}</code>'
                    for s in requires
                )
                st.markdown(
                    f'<div style="margin:0.6rem 0"><span style="color:{MUTED};'
                    f'font-size:0.82rem">Needs from the engine: </span>{chips}</div>',
                    unsafe_allow_html=True,
                )
                for s in requires:
                    if s in SIGNALS:
                        st.markdown(
                            f'<div style="color:{GREY};font-size:0.8rem;margin-left:0.4rem">'
                            f"— <code>{s}</code>: {SIGNALS[s]}</div>",
                            unsafe_allow_html=True,
                        )
                st.markdown(
                    f'<div style="color:{MUTED};font-size:0.78rem;margin-top:0.4rem">'
                    "If the engine cannot report one of these, the run stops at "
                    "startup rather than reading the missing value as zero.</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="color:{MUTED};font-size:0.82rem">'
                    "Needs nothing from the engine.</div>",
                    unsafe_allow_html=True,
                )

            c1, c2 = st.columns(2)
            if info.get("fits"):
                c1.markdown(
                    f'<div style="border-left:2px solid {YELLOW};padding-left:0.7rem">'
                    f'<div style="color:{WHITE};font-size:0.85rem;font-weight:600">'
                    f"When it fits</div>"
                    f'<div style="color:{GREY};font-size:0.83rem;margin-top:0.2rem">'
                    f"{info['fits']}</div></div>",
                    unsafe_allow_html=True,
                )
            if info.get("fails"):
                c2.markdown(
                    f'<div style="border-left:2px solid #FF5A52;padding-left:0.7rem">'
                    f'<div style="color:{WHITE};font-size:0.85rem;font-weight:600">'
                    f"When it misleads</div>"
                    f'<div style="color:{GREY};font-size:0.83rem;margin-top:0.2rem">'
                    f"{info['fails']}</div></div>",
                    unsafe_allow_html=True,
                )

            if info.get("source"):
                st.markdown("")
                st.markdown(f"**Source.** {info['source']}")
            if info.get("fidelity"):
                warn(info["fidelity"])

            st.markdown(
                f'<div style="color:{MUTED};font-size:0.76rem;margin-top:0.8rem">'
                f"<code>{cls.__module__}</code></div>",
                unsafe_allow_html=True,
            )

    section("Adding your own", "No change to this repository is needed.")
    st.code(
        """from admitperf import AdmissionPolicy, Decision, Request, SystemState


class MyPolicy(AdmissionPolicy):
    name = "my_policy"
    requires = frozenset({"waiting_requests"})

    def decide(self, req: Request, state: SystemState) -> Decision:
        if state.waiting_requests > 10:
            return Decision.reject(reason="queue_depth")
        return Decision.admit()""",
        language="python",
    )
    st.markdown("Then declare an entry point in your own package:")
    st.code(
        '[project.entry-points."admitperf.policies"]\nmy_policy = "my_pkg:MyPolicy"',
        language="toml",
    )
    st.markdown(
        f'<p style="color:{MUTED};font-size:0.8rem">'
        "Install it and it appears above, and in every experiment form.</p>",
        unsafe_allow_html=True,
    )


render()
