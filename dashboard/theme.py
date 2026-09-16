"""Shared look, so the app and the diagrams read as one project.

The palette is the one used by the C4 diagrams in docs/architecture: teal for
things that exist and work, amber for the entry point, red for refusals, slate
for context. Reusing it means a screenshot of this app sits beside a figure
from the docs without looking borrowed from somewhere else.
"""

from __future__ import annotations

TEAL = "#0f766e"
TEAL_LIGHT = "#ccfbf1"
AMBER = "#b45309"
AMBER_LIGHT = "#fef3c7"
RED = "#b91c1c"
RED_LIGHT = "#fee2e2"
BLUE = "#1e40af"
BLUE_LIGHT = "#dbeafe"
SLATE = "#64748b"
SLATE_LIGHT = "#f1f5f9"
INK = "#134e4a"

#: Categorical colours for policies. Ordered so the first few are
#: distinguishable in greyscale, since papers still get printed.
POLICY_COLORS = [TEAL, AMBER, BLUE, RED, "#7c3aed", "#0891b2", "#a16207", "#be185d"]

#: Decision outcomes keep a fixed colour everywhere: admitted is the good case,
#: rejected the refusal, deferred the middle. A reader should not have to
#: re-learn the legend between charts.
DECISION_COLORS = {"admit": TEAL, "defer": AMBER, "reject": RED}

CSS = f"""
<style>
  .block-container {{ padding-top: 2.2rem; max-width: 1400px; }}
  h1, h2, h3 {{ color: {INK}; letter-spacing: -0.01em; }}
  [data-testid="stMetricValue"] {{ color: {INK}; font-size: 1.7rem; }}
  [data-testid="stMetricLabel"] {{ color: {SLATE}; }}
  .ap-banner {{
    background: linear-gradient(90deg, {TEAL_LIGHT} 0%, #ffffff 70%);
    border-left: 4px solid {TEAL};
    padding: 0.9rem 1.2rem; border-radius: 6px; margin-bottom: 1.2rem;
  }}
  .ap-banner h1 {{ margin: 0; font-size: 1.6rem; }}
  .ap-banner p {{ margin: 0.25rem 0 0; color: {SLATE}; font-size: 0.92rem; }}
  .ap-warn {{
    background: {RED_LIGHT}; border-left: 4px solid {RED};
    padding: 0.7rem 1rem; border-radius: 6px; font-size: 0.9rem; color: #7f1d1d;
  }}
  .ap-note {{
    background: {SLATE_LIGHT}; border-left: 3px solid {SLATE};
    padding: 0.6rem 0.9rem; border-radius: 5px; font-size: 0.86rem; color: #334155;
  }}
</style>
"""


def policy_color_map(labels: list[str]) -> dict[str, str]:
    """Stable colour per policy, so a policy keeps its colour across charts."""
    return {label: POLICY_COLORS[i % len(POLICY_COLORS)] for i, label in enumerate(sorted(labels))}


__all__ = [
    "AMBER",
    "BLUE",
    "CSS",
    "DECISION_COLORS",
    "INK",
    "POLICY_COLORS",
    "RED",
    "SLATE",
    "TEAL",
    "policy_color_map",
]
