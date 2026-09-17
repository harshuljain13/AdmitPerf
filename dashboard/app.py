"""AdmitPerf — design, run and read admission-control experiments.

    streamlit run dashboard/app.py

Three pages in the order the work happens: design an experiment, run it, then
read what came out. Each is a separate module under pages/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from theme import CSS, chart_theme  # noqa: E402

st.set_page_config(
    page_title="AdmitPerf",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
# The wordmark, pinned above the navigation. Rendered from docs/assets/logo.svg
# to PNG at build time: st.logo takes an image, and SVG text would depend on
# Helvetica being present in whatever browser opens this. icon_image is the
# collapsed-rail mark, where the full lockup does not fit.
ASSETS = HERE.parent / "docs" / "assets"
if (ASSETS / "logo.png").exists():
    st.logo(
        str(ASSETS / "logo.png"),
        size="large",
        icon_image=str(ASSETS / "icon.png"),
        link="https://github.com/harshuljain13/AdmitPerf",
    )

st.markdown(CSS, unsafe_allow_html=True)

# Charts on a light ground inside a dark page look borrowed from another
# application. The theme API moved in altair 5.5; both spellings are handled so
# the app runs against whatever the lock resolves.
try:
    alt.theme.register("admitperf", enable=True)(chart_theme)
except AttributeError:  # pragma: no cover - altair < 5.5
    alt.themes.register("admitperf", chart_theme)
    alt.themes.enable("admitperf")

# Grouped in the order the work happens: set something up, measure it, then
# look things up when a term or a policy is unfamiliar.
# Pages live in views/, not pages/. "pages/" is a magic directory: Streamlit
# auto-discovers it and derives titles from filenames, so when st.navigation
# below fails for any reason the app silently falls back to that — and the
# sidebar fills with lowercase filenames instead of these titles. Two page
# mechanisms competing over one directory is not worth the convenience.
#
# Icons are Material names, not emoji. st.Page rejects plain Unicode symbols
# such as "✎", and the failure only surfaces when the page is rendered — the
# server still returns 200.
#
# File name, page title and URL all match. Anything else is a trap for whoever
# reads the sidebar and then goes looking for the file.
st.navigation(
    {
        "Set Up": [
            st.Page(
                HERE / "views" / "experiments.py",
                title="Experiments",
                icon=":material/science:",
                url_path="experiments",
                default=True,
            ),
            st.Page(
                HERE / "views" / "algorithms.py",
                title="Algorithms",
                icon=":material/tune:",
                url_path="algorithms",
            ),
        ],
        "Measure": [
            st.Page(
                HERE / "views" / "run.py", title="Run", icon=":material/play_arrow:", url_path="run"
            ),
            st.Page(
                HERE / "views" / "results.py",
                title="Results",
                icon=":material/bar_chart:",
                url_path="results",
            ),
        ],
        "Reference": [
            st.Page(
                HERE / "views" / "terminology.py",
                title="Terminology",
                icon=":material/menu_book:",
                url_path="terminology",
            ),
        ],
    }
).run()
