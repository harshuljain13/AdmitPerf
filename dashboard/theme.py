"""AdmitPerf branding, taken from the banner rather than invented.

Every value here is read off `docs/assets/banner.svg`: near-black ground,
admit-yellow wordmark, white second half, grey tagline, a thin yellow accent
bar, Helvetica at weight 900 with tight tracking.

One consequence worth stating, because it drives the chart palette: the
wordmark spells *Admit* in yellow. So yellow means admitted everywhere in this
app — the brand colour and the positive outcome are the same thing, which
makes the legend one less thing to learn.
"""

from __future__ import annotations

# --- straight from banner.svg --------------------------------------------

INK = "#0B0B0B"  # bg
YELLOW = "#F5C518"  # wordmark "Admit", accent bar
WHITE = "#FFFFFF"  # wordmark "Perf"
GREY = "#BFBFBF"  # tagline
FONT = "Helvetica, Arial, sans-serif"

# Derived, staying inside the same family.
SURFACE = "#141414"  # cards and sidebar, a shade off the ground
LINE = "#2A2A2A"  # hairlines
MUTED = "#8A8A8A"  # secondary text
YELLOW_DIM = "#8A6F0E"  # yellow at rest, for non-highlighted bars

# Outcomes. Red and amber are picked for legibility on near-black rather than
# taken from the banner, which has no failure state to borrow.
RED = "#FF5A52"
AMBER = "#FFA23A"

#: Admit is the brand yellow on purpose — see module docstring.
DECISION_COLORS = {"admit": YELLOW, "defer": AMBER, "reject": RED}

#: Categorical colours for policies, all checked against the near-black ground.
#: Yellow leads so the first policy plotted carries the brand colour.
POLICY_COLORS = [
    YELLOW,
    "#5AC8FA",
    "#FF5A52",
    "#7ED957",
    "#C98BFF",
    "#FFA23A",
    "#4DD0C1",
    "#FF7BB0",
]


def policy_color_map(labels: list[str]) -> dict[str, str]:
    """Stable colour per policy, so one keeps its colour across every chart."""
    return {label: POLICY_COLORS[i % len(POLICY_COLORS)] for i, label in enumerate(sorted(labels))}


def chart_theme() -> dict:
    """Altair defaults. Charts on a light ground inside a dark page look
    borrowed from another application, so the whole surface is carried
    through."""
    return {
        "config": {
            "background": "transparent",
            "font": FONT,
            "title": {"color": WHITE, "fontSize": 14, "fontWeight": 600, "anchor": "start"},
            "axis": {
                "labelColor": GREY,
                "titleColor": MUTED,
                "gridColor": LINE,
                "domainColor": LINE,
                "tickColor": LINE,
                "labelFontSize": 11,
                "titleFontSize": 11,
                "titleFontWeight": 400,
            },
            "legend": {
                "labelColor": GREY,
                "titleColor": MUTED,
                "labelFontSize": 11,
                "titleFontSize": 11,
                "symbolType": "square",
            },
            "view": {"stroke": "transparent"},
            "range": {"category": POLICY_COLORS},
        }
    }


CSS = f"""
<style>
  html, body, [data-testid="stAppViewContainer"] {{
    background: {INK};
    font-family: {FONT};
  }}
  .block-container {{ padding-top: 1.6rem; max-width: 1440px; }}

  h1, h2, h3, h4 {{ color: {WHITE}; font-family: {FONT}; letter-spacing: -0.02em; }}
  p, li, label, span {{ color: {GREY}; }}

  [data-testid="stSidebar"] {{
    background: {SURFACE};
    border-right: 1px solid {LINE};
  }}
  /* Navigation group headings ("Set up", "Measure", "Reference"). */
  [data-testid="stSidebarNav"] span {{ font-size: 0.88rem; }}
  [data-testid="stSidebarNavSeparator"] {{ border-color: {LINE}; }}
  [data-testid="stSidebar"] h2 {{
    color: {YELLOW}; font-weight: 900; letter-spacing: 0.04em;
    text-transform: uppercase; font-size: 0.78rem;
  }}

  /* Metric cards: one thin yellow rule, echoing the banner's accent bar. */
  [data-testid="stMetric"] {{
    background: {SURFACE};
    border: 1px solid {LINE};
    border-top: 2px solid {YELLOW};
    border-radius: 4px;
    padding: 0.85rem 1rem;
  }}
  [data-testid="stMetricValue"] {{
    color: {WHITE}; font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em;
  }}
  [data-testid="stMetricLabel"] p {{
    color: {MUTED}; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.08em;
  }}

  /* The sidebar carries the wordmark, so pages lead with their own title
     rather than repeating it. */
  [data-testid="stSidebarHeader"] {{ padding-bottom: 0.4rem; }}
  /* Fill the rail rather than float in it. Streamlit caps st.logo height, so
     the width is set here and the height left to follow. */
  [data-testid="stLogo"] {{
    width: 200px; max-width: 88%; height: auto; margin: 0.35rem 0 0.2rem;
  }}

  .ap-header {{ margin: 0 0 0.4rem; }}
  .ap-wordmark {{
    font-family: {FONT}; font-weight: 900; font-size: 2.6rem;
    letter-spacing: -0.055em; line-height: 1; margin: 0;
  }}
  .ap-wordmark .a {{ color: {YELLOW}; }}
  .ap-wordmark .p {{ color: {WHITE}; }}
  .ap-tagline {{
    color: {GREY}; font-size: 0.74rem; letter-spacing: 0.22em;
    text-transform: lowercase; margin: 0.45rem 0 0;
  }}
  .ap-accent {{
    width: 160px; height: 4px; background: {YELLOW};
    margin: 0.85rem 0 1.3rem; border-radius: 1px;
  }}

  .ap-note {{
    background: {SURFACE}; border-left: 3px solid {YELLOW};
    padding: 0.65rem 0.95rem; border-radius: 3px;
    font-size: 0.84rem; color: {GREY};
  }}
  .ap-warn {{
    background: #1C1010; border-left: 3px solid {RED};
    padding: 0.7rem 1rem; border-radius: 3px;
    font-size: 0.84rem; color: #FFC9C5;
  }}

  .stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid {LINE}; }}
  .stTabs [data-baseweb="tab"] {{
    color: {MUTED}; font-size: 0.86rem; font-weight: 600;
    letter-spacing: 0.02em; padding: 0.4rem 0;
  }}
  .stTabs [aria-selected="true"] {{ color: {YELLOW}; }}
  .stTabs [data-baseweb="tab-highlight"] {{ background: {YELLOW}; }}

  [data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 4px; }}
  hr {{ border-color: {LINE}; }}

  /* Secondary buttons: yellow outline on the dark ground. */
  .stButton>button, .stDownloadButton>button {{
    background: transparent; color: {YELLOW};
    border: 1px solid {YELLOW}; border-radius: 3px;
    font-weight: 600; letter-spacing: 0.02em;
  }}
  .stButton>button:hover, .stDownloadButton>button:hover {{
    background: {YELLOW}; color: {INK}; border-color: {YELLOW};
  }}

  /* Anything Streamlit fills with primaryColor needs near-black text on top.
     Left alone it uses the theme's own foreground, which in a dark theme is
     white — white on #F5C518 is barely readable. Several selectors because the
     test id has changed across versions. */
  .stButton>button[kind="primary"],
  .stButton>button[kind="primaryFormSubmit"],
  .stDownloadButton>button[kind="primary"],
  button[data-testid="baseButton-primary"],
  button[data-testid="stBaseButton-primary"],
  [data-testid="stFormSubmitButton"] button {{
    background: {YELLOW} !important;
    color: {INK} !important;
    border: 1px solid {YELLOW} !important;
    font-weight: 700;
  }}
  .stButton>button[kind="primary"]:hover,
  button[data-testid="baseButton-primary"]:hover,
  button[data-testid="stBaseButton-primary"]:hover {{
    background: #FFD84D !important;
    color: {INK} !important;
    border-color: #FFD84D !important;
  }}

  /* The slider's value bubble is also painted with primaryColor. */
  [data-testid="stThumbValue"], [data-testid="stSliderThumbValue"] {{
    color: {YELLOW};
  }}
  [data-baseweb="slider"] [role="slider"] {{ background: {YELLOW}; }}

  /* Progress bars carry no text, but keep them on-brand. */
  [data-testid="stProgress"] > div > div > div > div {{ background: {YELLOW}; }}
</style>
"""

#: The wordmark, rebuilt in HTML rather than inlining banner.svg. The SVG is a
#: fixed 1280x260 lockup; this scales with the viewport and matches it
#: glyph-for-glyph — same family, same weight, same tracking, same split.
HEADER = """
<div class="ap-header">
  <p class="ap-wordmark"><span class="a">Admit</span><span class="p">Perf</span></p>
  <p class="ap-tagline">benchmark-driven admission control layer for LLM inference</p>
  <div class="ap-accent"></div>
</div>
"""

__all__ = [
    "AMBER",
    "CSS",
    "DECISION_COLORS",
    "FONT",
    "GREY",
    "HEADER",
    "INK",
    "LINE",
    "MUTED",
    "POLICY_COLORS",
    "RED",
    "SURFACE",
    "WHITE",
    "YELLOW",
    "YELLOW_DIM",
    "chart_theme",
    "policy_color_map",
]
