"""Run an experiment and watch it happen.

Shells out to `admitperf bench run` rather than importing the harness. Two
reasons: the CLI is the supported path and this page should exercise it, not a
parallel one; and a long benchmark inside the Streamlit process would block the
whole app, whereas a subprocess can be watched and cancelled.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from components import empty_state, section, verdict, warn  # noqa: E402
from theme import GREY, HEADER, LINE, MUTED, SURFACE, WHITE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "experiments"
RESULTS = ROOT / "results"


def _fake_engine_running(port: int = 8000) -> bool:
    import httpx

    try:
        return httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def render() -> None:
    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Run")

    configs = sorted(EXPERIMENTS.glob("*.yaml")) if EXPERIMENTS.exists() else []
    if not configs:
        empty_state(
            "No Experiments Yet",
            "Design one first — the Design page writes a config here.",
            "",
        )
        return

    names = [c.name for c in configs]
    last = st.session_state.get("last_config")
    default = names.index(Path(last).name) if last and Path(last).name in names else 0
    chosen = st.selectbox("Experiment", names, index=default)
    config_path = EXPERIMENTS / chosen

    section("Where To Run It", "")

    target = st.radio(
        "Engine",
        ["Fake engine (free, no GPU)", "Already running (paste a URL)", "Provisioned session"],
        help="The fake engine proves the wiring and costs nothing, but none of "
        "its timings mean anything about hardware. Real numbers need a real "
        "engine.",
    )

    engine_url = None
    if target.startswith("Fake"):
        port = st.number_input("Port", 1024, 65535, 8000)
        engine_url = f"http://127.0.0.1:{port}"
        if _fake_engine_running(int(port)):
            st.success(f"Fake engine responding on port {port}.")
        else:
            warn(f"Nothing is serving on port {port}. Start it in a terminal first:")
            st.code(f"python scripts/fake_vllm.py --port {port} --capacity 4", language="bash")
    elif target.startswith("Already"):
        engine_url = st.text_input("Engine URL", "http://127.0.0.1:8000")
    else:
        session = ROOT / ".admitperf" / "session.json"
        if session.exists():
            st.success("Using the provisioned session.")
        else:
            warn("No session found. Provision one first:")
            st.code("admitperf infra up -c experiments/" + chosen, language="bash")

    c1, c2 = st.columns(2)
    out_name = c1.text_input("Results folder", f"{config_path.stem}")
    override_repeats = c2.number_input("Override repeats (0 = use config)", 0, 10, 0)

    if target.startswith("Fake") and not _fake_engine_running(
        int(engine_url.rsplit(":", 1)[1]) if engine_url else 8000
    ):
        st.button("Run", disabled=True, width="stretch")
        return

    if st.button("▸  Run experiment", type="primary", width="stretch"):
        cmd = [
            sys.executable,
            "-m",
            "admitperf.cli",
            "bench",
            "run",
            "-c",
            str(config_path),
            "--out",
            str(RESULTS / out_name),
        ]
        if engine_url:
            cmd += ["--engine-url", engine_url]
        if override_repeats:
            cmd += ["--repeats", str(override_repeats)]

        st.code(" ".join(cmd[2:]), language="bash")
        log = st.empty()
        lines: list[str] = []
        started = time.time()

        with st.status("Running…", expanded=True) as status:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip())
                # Tail rather than the whole log: a long sweep produces
                # hundreds of lines and only the recent ones are useful while
                # it is still going.
                log.code("\n".join(lines[-18:]))
            code = proc.wait()

            elapsed = time.time() - started
            if code == 0:
                status.update(label=f"Finished in {elapsed:.0f}s", state="complete")
            else:
                status.update(label=f"Failed (exit {code})", state="error")

        if code == 0:
            st.session_state["last_results"] = str(RESULTS / out_name)
            verdict(
                "Run complete",
                f"{len(lines)} lines of output in {elapsed:.0f}s. "
                "Open the <b>Results</b> page to see what happened.",
            )
        else:
            warn(
                "<b>The run failed.</b> The last lines above usually say why — a "
                "missing engine, a policy setting it does not accept, or a "
                "config that cannot produce a working deployment."
            )

    section("Recent Results", "")
    if RESULTS.exists():
        recent = sorted(
            (d for d in RESULTS.iterdir() if d.is_dir()),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )[:6]
        for d in recent:
            runs = len(list(d.rglob("summary.json")))
            st.markdown(
                f'<div style="background:{SURFACE};border:1px solid {LINE};'
                f'border-radius:4px;padding:0.6rem 0.9rem;margin-bottom:0.4rem">'
                f'<span style="color:{WHITE};font-weight:600">{d.name}</span>'
                f'<span style="color:{MUTED};font-size:0.82rem"> — {runs} runs</span>'
                "</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f'<p style="color:{GREY};font-size:0.85rem">Nothing yet.</p>',
            unsafe_allow_html=True,
        )


render()
