"""Shared UI pieces, so every page states things the same way.

The rule behind most of these: say the finding in words before showing the
number. A table of metrics answers "what were the values"; a reader almost
always arrives with "which one should I use, and can I trust it".
"""

from __future__ import annotations

import streamlit as st
from theme import GREY, LINE, MUTED, RED, SURFACE, WHITE, YELLOW


def section(title: str, caption: str = "") -> None:
    st.markdown(
        f'<div style="margin:1.6rem 0 0.7rem">'
        f'<div style="color:{WHITE};font-size:1.05rem;font-weight:700;'
        f'letter-spacing:-0.01em">{title}</div>'
        + (
            f'<div style="color:{MUTED};font-size:0.82rem;margin-top:0.15rem">{caption}</div>'
            if caption
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def verdict(headline: str, detail: str, *, tone: str = "good") -> None:
    """The answer, in a sentence, before any chart.

    Tone is about confidence, not about whether the result is flattering: a
    finding that cannot be trusted is shown in the warning colour however good
    the numbers look.
    """
    accent = {"good": YELLOW, "warn": RED, "flat": MUTED}[tone]
    st.markdown(
        f'<div style="background:{SURFACE};border:1px solid {LINE};'
        f"border-left:3px solid {accent};border-radius:4px;padding:1rem 1.2rem;"
        f'margin:0.2rem 0 1.1rem">'
        f'<div style="color:{WHITE};font-size:1.12rem;font-weight:700;'
        f'letter-spacing:-0.01em;line-height:1.35">{headline}</div>'
        f'<div style="color:{GREY};font-size:0.87rem;margin-top:0.4rem;'
        f'line-height:1.5">{detail}</div></div>',
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="ap-note">{text}</div>', unsafe_allow_html=True)


def warn(text: str) -> None:
    st.markdown(f'<div class="ap-warn">{text}</div>', unsafe_allow_html=True)


def stat(label: str, value: str, help_text: str = "") -> None:
    st.metric(label, value, help=help_text or None)


def empty_state(title: str, body: str, code: str = "") -> None:
    """What to do next, rather than an error. A blank page with a stack trace
    tells someone they did something wrong; this tells them the next step."""
    st.markdown(
        f'<div style="background:{SURFACE};border:1px dashed {LINE};border-radius:6px;'
        f'padding:2rem;text-align:center;margin:1.5rem 0">'
        f'<div style="color:{WHITE};font-size:1.05rem;font-weight:700">{title}</div>'
        f'<div style="color:{MUTED};font-size:0.88rem;margin-top:0.5rem">{body}</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if code:
        st.code(code, language="bash")


__all__ = ["empty_state", "note", "section", "stat", "verdict", "warn"]
