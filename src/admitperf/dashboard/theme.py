"""The palette, taken from the banner rather than invented.

Every colour here appears in `assets/banner.svg`: near-black ground, admit-yellow,
white, grey. The wordmark spells *Admit* in yellow, which is why yellow also means
admitted in every chart — a reader should not have to learn a second key.
"""

from __future__ import annotations

INK = "#0B0B0B"  # ground
SURFACE = "#141414"  # cards and sidebar, a shade off the ground
LINE = "#2A2A2A"  # hairlines
YELLOW = "#F5C518"  # accent, and "admitted"
YELLOW_DIM = "#8A6F0E"  # yellow at rest, for an un-highlighted series
WHITE = "#FFFFFF"
GREY = "#BFBFBF"  # secondary text
MUTED = "#8A8A8A"
RED = "#FF5A52"  # refused, and a failed check
AMBER = "#FFA23A"  # deferred, and a caveat
GREEN = "#4ED17F"  # a passed check

FONT = "Helvetica, Arial, sans-serif"

#: Yellow on white is 1.63:1, far under the 4.5:1 minimum, so anything sitting on the
#: accent colour takes near-black text rather than the theme's default white.
ON_YELLOW = INK

CSS = f"""
<style>
  .stApp {{ background:{INK}; }}
  /* Sidebar: the wordmark, navigation, and the experiment — the things that scope the
     whole page. Narrow, because everything else moved inline with the content it
     changes. The RIGHT-hand panel is what was squeezing the charts, and it is gone. */
  section[data-testid="stSidebar"] {{
    background:{SURFACE}; border-right:1px solid {LINE};
    width:15rem !important; min-width:15rem !important; max-width:15rem !important;
  }}
  /* THE reason the wordmark kept sitting low. Streamlit reserves an empty header slot
     at the top of the sidebar for st.logo and the collapse button; our markup goes
     below it. Verified against the ids in the installed bundle rather than guessed:
     stSidebarHeader, stSidebarContent, stSidebarUserContent. */
  [data-testid="stSidebarHeader"] {{
    display:none !important; height:0 !important; padding:0 !important; margin:0 !important;
  }}
  [data-testid="stSidebarContent"],
  [data-testid="stSidebarUserContent"] {{ padding-top:1.1rem !important; }}
  section[data-testid="stSidebar"] > div:first-child {{ padding-top:0; }}
  section[data-testid="stSidebar"] label {{
    color:{MUTED} !important; font-size:0.68rem !important;
    text-transform:uppercase; letter-spacing:0.09em;
  }}
  /* Nav items are buttons we own, so the wordmark above them is genuinely first.
     st.navigation renders into Streamlit's own slot, above anything we write. */
  /* Left-justified. `justify-content` on the button alone is not enough: Streamlit
     wraps the label in a markdown container with its own centring, so the inner
     elements have to be told too. Test ids taken from the installed bundle
     (stButton, stBaseButton, stMarkdownContainer) rather than guessed. */
  section[data-testid="stSidebar"] [data-testid="stButton"],
  section[data-testid="stSidebar"] [data-testid="stButton"] > div {{
    text-align:left; width:100%;
  }}
  section[data-testid="stSidebar"] button {{
    font-family:{FONT}; font-size:0.95rem; font-weight:600;
    display:flex; justify-content:flex-start !important; text-align:left;
    border:none; padding-left:0.7rem; margin-bottom:0.15rem; width:100%;
  }}
  section[data-testid="stSidebar"] button > div,
  section[data-testid="stSidebar"] button [data-testid="stMarkdownContainer"],
  section[data-testid="stSidebar"] button p {{
    text-align:left !important; justify-content:flex-start !important;
    width:100%; margin:0;
  }}
  section[data-testid="stSidebar"] button[kind="secondary"] {{
    background:transparent; color:{GREY};
  }}
  .ap-mark-side {{
    font-family:{FONT}; font-weight:900; font-size:1.85rem; line-height:1;
    letter-spacing:-0.035em; margin:0 0 1rem 0;
  }}

  /* Streamlit stacks a toolbar, a decoration bar and a sidebar-collapse control above
     the content. With no sidebar to collapse they are all dead chrome, and they are
     what sits between the top-left corner and the wordmark. */
  [data-testid="stHeader"], header[data-testid="stHeader"] {{
    display:none !important; height:0 !important;
  }}
  [data-testid="stToolbar"],
  [data-testid="stDecoration"],
  [data-testid="stDecoration"] {{ display:none !important; }}
  [data-testid="stMainBlockContainer"], .block-container {{
    padding-top:1.1rem !important; padding-left:1.6rem; max-width:none;
  }}

  h1,h2,h3,h4,h5 {{ font-family:{FONT}; letter-spacing:-0.02em; }}

  /* The control bar: one row above the content, so page-level choices are in one
     place without costing a column. */
  .ap-bar {{
    background:{SURFACE}; border:1px solid {LINE}; border-radius:10px;
    padding:0.35rem 0.9rem 0.1rem 0.9rem; margin:0.25rem 0 1.4rem 0;
  }}
  .ap-bar label {{ color:{MUTED} !important; font-size:0.7rem !important;
                   text-transform:uppercase; letter-spacing:0.08em; }}

  /* --- the finding: the largest thing on the page ------------------ */
  .ap-answer {{
    font-family:{FONT}; font-size:1.55rem; line-height:1.45; font-weight:600;
    color:{WHITE}; border-left:4px solid {YELLOW}; padding:0.15rem 0 0.15rem 1.1rem;
    margin:0.2rem 0 1.5rem 0;
  }}
  .ap-answer b {{ color:{YELLOW}; }}
  .ap-answer code {{ color:{YELLOW}; background:transparent; font-size:1.4rem; }}

  /* --- cards ------------------------------------------------------- */
  .ap-card {{
    background:{SURFACE}; border:1px solid {LINE}; border-radius:10px;
    padding:0.85rem 1.05rem; height:100%;
  }}
  .ap-card .k {{ color:{MUTED}; font-size:0.68rem; text-transform:uppercase;
                 letter-spacing:0.09em; }}
  .ap-card .v {{ color:{WHITE}; font-family:{FONT}; font-size:1.8rem;
                 font-weight:700; line-height:1.25; }}
  .ap-card .d {{ font-size:0.82rem; }}

  /* Section headings earn their weight: a rule above, so six sections of content
     stop reading as one undifferentiated column. */
  .ap-h {{
    font-family:{FONT}; font-size:1.05rem; font-weight:700; color:{WHITE};
    border-top:1px solid {LINE}; padding-top:1.1rem; margin:2rem 0 0.2rem 0;
  }}
  .ap-h span {{ color:{MUTED}; font-weight:500; font-size:0.82rem; }}

  .ap-check {{ font-family:{FONT}; font-size:0.9rem; color:{GREY}; padding:0.15rem 0; }}

  /* Primary marks the active section. White on admit-yellow is 1.63:1, far under the
     4.5:1 minimum, and Streamlit defaults to white on a dark theme. */
  button[kind="primary"], button[kind="primary"] p {{ color:{ON_YELLOW} !important; }}
  [data-baseweb="segmented-control"] {{ background:{SURFACE}; }}
</style>
"""


def axis() -> dict:
    """Altair config, so charts sit on the ground rather than on a white card."""
    return {
        "background": INK,
        "font": FONT,
        "axis": {
            "labelColor": GREY,
            "titleColor": MUTED,
            "gridColor": LINE,
            "domainColor": LINE,
            "tickColor": LINE,
        },
        "legend": {"labelColor": GREY, "titleColor": MUTED},
        "view": {"stroke": "transparent"},
    }
