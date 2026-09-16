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
# File name, page title and URL all match. Anything else is a trap for whoever
# reads the sidebar and then goes looking for the file.
st.navigation(
    {
        "Set Up": [
            st.Page(
                HERE / "pages" / "experiments.py",
                title="Experiments",
                icon="✎",
                url_path="experiments",
                default=True,
            ),
            st.Page(
                HERE / "pages" / "algorithms.py",
                title="Algorithms",
                icon="⚙",
                url_path="algorithms",
            ),
        ],
        "Measure": [
            st.Page(HERE / "pages" / "run.py", title="Run", icon="▸", url_path="run"),
            st.Page(HERE / "pages" / "results.py", title="Results", icon="▤", url_path="results"),
        ],
        "Reference": [
            st.Page(
                HERE / "pages" / "terminology.py",
                title="Terminology",
                icon="≡",
                url_path="terminology",
            ),
        ],
    }
).run()
