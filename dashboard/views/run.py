"""Run an experiment: a pipeline, not a button.

A configured experiment becomes a fixed sequence — provision the machine, run
the policies against it, aggregate what came back, write the report — drawn as
a single rail that is the plan before you start and the progress display once
you do. One representation, not two: the same rows fill in rather than being
replaced by something that looks different.

It shells out to `admitperf` rather than importing the harness. The CLI is the
supported path, so this page should exercise it rather than a parallel one, and
a twenty-minute benchmark stays in a subprocess instead of blocking the app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from components import empty_state, note, section, verdict, warn  # noqa: E402
from theme import (  # noqa: E402
    GREY,
    HEADER,
    INK,
    LINE,
    MUTED,
    RED,
    SURFACE,
    WHITE,
    YELLOW,
    YELLOW_DIM,
)

from admitperf.core.config import ConfigError, ExperimentConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / "experiments"
RESULTS = ROOT / "results"
SESSION = ROOT / ".admitperf" / "session.json"

#: Lines of a stage's output kept on screen. A deploy prints hundreds and only
#: the recent ones say anything about what it is doing now.
TAIL = 18


#: How often the rail is redrawn while a stage is streaming. A redraw rebuilds
#: the whole rail, so doing it per line would spend more time rendering than
#: running.
REDRAW_EVERY_S = 0.3

#: marker, colour. A dot for what happened, a ring for what is happening, a
#: hollow ring for what was deliberately not done.
MARKERS = {
    "waiting": ("·", MUTED),
    "running": ("◍", YELLOW),
    "done": ("●", YELLOW),
    "skipped": ("○", MUTED),
    "failed": ("●", RED),
}


@dataclass
class Stage:
    """One step of the pipeline, and what it means when it fails."""

    title: str
    detail: str
    cmd: list[str]
    #: Why this stage will not run, if it will not. Shown in the plan, so a
    #: skipped step is visibly a decision rather than an omission.
    skip: str = ""
    #: Teardown runs even after an earlier failure — a run that breaks
    #: part-way through is exactly when a GPU gets left running.
    always: bool = False
    on_fail: str = ""
    output: list[str] = field(default_factory=list)
    state: str = "waiting"
    elapsed: float = 0.0


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "admitperf.cli", *args]


def _mock_engine_running(port: int = 8000) -> bool:
    import httpx

    try:
        return httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1.0).status_code == 200
    except httpx.HTTPError:
        return False


def _session_summary() -> dict[str, object] | None:
    try:
        return json.loads(SESSION.read_text())
    except (OSError, ValueError):
        return None


def _load(config_path: Path) -> ExperimentConfig | None:
    try:
        return ExperimentConfig.load(config_path)
    except ConfigError as exc:
        warn(f"<b>This config will not run.</b> {exc}")
        return None


def _plan(
    *,
    config_path: Path,
    cfg: ExperimentConfig,
    out_dir: Path,
    engine_url: str | None,
    repeats: int,
    provision: bool,
    reuse: bool,
    teardown: bool,
) -> list[Stage]:
    """The whole sequence, including the parts that will be skipped.

    Built once and used for both the preview and the run, so what the page
    promises and what it does cannot drift apart.
    """
    config = str(config_path)
    session = _session_summary()

    bench = _cli("bench", "run", "-c", config, "--out", str(out_dir))
    if engine_url:
        bench += ["--engine-url", engine_url]
    if repeats:
        bench += ["--repeats", str(repeats)]

    stages = [
        Stage(
            "Provision",
            f"{cfg.infra.model} on {cfg.infra.modal_gpu}, via {cfg.infra.provider}",
            _cli("infra", "up", "-c", config),
            skip=(
                ""
                if provision and not reuse
                else "reusing the existing session"
                if provision
                else "you are pointing at an engine that is already running"
            ),
            on_fail="Nothing was run and no session was written. Common causes, "
            "if the output above does not already say: no payment method on the "
            "Modal account (GPU functions are refused outright), a GPU type "
            "unavailable in your region, gated weights needing an HF token, or a "
            "tensor-parallel size larger than the GPUs requested. If the deploy "
            "got far enough to create an app, <code>modal app list</code> will "
            "show it.",
        ),
        Stage(
            "Check it answers",
            "waits for the cold start, then one request end to end",
            _cli("infra", "smoke"),
            skip="" if provision else "not this page's deployment to vouch for",
            on_fail="The deployment exists but never served a request, so no "
            "benchmark was run against it. If it timed out, the engine may still "
            "be loading weights — <code>modal app logs admitperf-vllm</code> will "
            "say. A failed metrics scrape in particular means KV-pressure policies "
            "would have had nothing to decide on.",
        ),
        Stage(
            "Calibrate",
            "unloaded latency, which relative SLOs are multiples of",
            _cli("infra", "calibrate"),
            skip=(
                "this config's SLOs are absolute"
                if cfg.workload.slo_mode != "relative"
                else "a baseline is already saved with the session"
                if (session or {}).get("baseline")
                else ""
            ),
            on_fail="This config's SLOs are relative to the unloaded baseline, so "
            "without one the benchmark could not have scored anything.",
        ),
        Stage(
            "Run the experiment",
            f"{len(cfg.policies)} policies x {repeats or cfg.bench.repeats} repeats, "
            "same engine, same seeded traffic",
            bench,
            on_fail="The last lines above usually say why — a policy setting the "
            "engine will not accept, or an engine that stopped answering "
            "part-way through.",
        ),
        Stage(
            "Aggregate",
            "medians and spread per policy, grouped by deployment",
            _cli("bench", "compare", str(out_dir)),
            on_fail="The runs finished but could not be aggregated. Usually a "
            "bundle was written half-way when something was interrupted.",
        ),
        Stage(
            "Write the report",
            "a self-contained report.html, plus compare.txt and figures/",
            _cli("bench", "report", str(out_dir)),
            on_fail="The numbers are on disk and readable on the Results page; "
            "only the report artifact is missing. <code>pip install -e "
            "'.[analysis]'</code> if matplotlib is what it complained about.",
        ),
        Stage(
            "Tear down",
            "stop the deployment so it stops billing",
            _cli("infra", "down"),
            skip=""
            if (provision and teardown)
            else "you asked to keep the deployment up"
            if provision
            else "nothing was provisioned here",
            always=True,
            on_fail="<b>The GPU may still be running and billing.</b> Check it "
            "yourself: <code>modal app list</code>, then <code>admitperf infra down</code>.",
        ),
    ]
    for stage in stages:
        if stage.skip:
            stage.state = "skipped"
    return stages


def _elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


#: Injected once per rail draw. Animations are CSS rather than redraws so that
#: a spinner keeps spinning while the rail itself sits still.
_ANIM = (
    "<style>"
    "@keyframes ap-spin { to { transform: rotate(360deg); } }"
    ".ap-ring { position:absolute; inset:0; border-radius:50%;"
    "  border:2px solid rgba(245,197,24,0.22);"
    f"  border-top-color:{YELLOW};"
    "  animation: ap-spin 0.85s linear infinite; }"
    "</style>"
)


def _marker(stage: Stage, number: int | None) -> str:
    """A numbered slot that becomes a tick.

    The number is the answer to "which steps will actually run" — skipped
    stages never get one, so the sequence you can read down the rail is exactly
    what is going to happen.
    """
    base = (
        "position:relative;display:inline-flex;align-items:center;"
        "justify-content:center;width:1.25rem;height:1.25rem;border-radius:50%;"
        "font-size:0.68rem;font-weight:700;box-sizing:border-box;line-height:1;"
    )
    if stage.state == "done":
        return f'<span style="{base}background:{YELLOW};color:{INK}">✓</span>'
    if stage.state == "failed":
        return f'<span style="{base}background:{RED};color:{WHITE}">✕</span>'
    if stage.state == "running":
        return (
            f'<span style="{base}color:{YELLOW};background:{INK}">'
            f'<span class="ap-ring"></span>{number}</span>'
        )
    if stage.state == "skipped":
        return (
            f'<span style="{base}border:1px dashed {LINE};color:{MUTED};background:{INK}">–</span>'
        )
    return f'<span style="{base}border:1px solid {LINE};color:{MUTED};background:{INK}">{number}</span>'


def _status_text(stage: Stage) -> tuple[str, str]:
    """The right-hand column: what this stage is doing, or why it is not."""
    if stage.state == "skipped":
        return f"skipped — {stage.skip}", MUTED
    if stage.state == "running":
        return "running", YELLOW
    if stage.state == "done":
        return f"done · {_elapsed(stage.elapsed)}", GREY
    if stage.state == "failed":
        return f"failed · {_elapsed(stage.elapsed)}", RED
    return "will run", GREY


def _row(stage: Stage, number: int | None, *, last: bool) -> str:
    status, status_colour = _status_text(stage)
    skipped = stage.state == "skipped"
    title_colour = MUTED if skipped else WHITE
    # The connector is the row's own left border, so the rail is continuous by
    # construction. It carries the colour of what has already happened, which
    # is how far down the page the run has got.
    done_here = stage.state in ("done", "failed", "running")
    rail = "none" if last else f"1px solid {YELLOW_DIM if done_here else LINE}"
    return (
        f'<div style="position:relative;border-left:{rail};margin-left:0.6rem;'
        f"padding:0 0 0.75rem 1.4rem;min-height:1.7rem;"
        f'{"opacity:0.55;" if skipped else ""}">'
        f'<span style="position:absolute;left:-0.63rem;top:-0.15rem">'
        f"{_marker(stage, number)}</span>"
        f'<div style="display:flex;justify-content:space-between;gap:1rem;'
        f'align-items:baseline">'
        f'<span style="color:{title_colour};font-weight:600;font-size:0.92rem">'
        f"{stage.title}</span>"
        f'<span style="color:{status_colour};font-size:0.78rem;'
        f'white-space:nowrap">{status}</span></div>'
        f'<div style="color:{MUTED};font-size:0.78rem;margin-top:0.12rem">'
        f"{stage.detail}</div></div>"
    )


def _draw_rail(stages: list[Stage], slot, *, started: float | None) -> None:
    """Draw the rail. Called on state changes only, not per line of output —
    a redraw restarts every CSS animation on it, and a spinner that restarts
    three times a second reads as a glitch."""
    runnable = [s for s in stages if s.state != "skipped"]
    finished = [s for s in runnable if s.state in ("done", "failed")]
    numbers = {id(s): i for i, s in enumerate(runnable, 1)}

    if started and any(s.state == "running" for s in runnable):
        # The rail only redraws between stages, so a clock here would sit
        # frozen for the length of a deploy. The live panel below carries the
        # one that ticks.
        progress = f"{len(finished)} of {len(runnable)} done"
    elif started:
        progress = f"{len(finished)} of {len(runnable)} · {_elapsed(time.time() - started)}"
    else:
        skipped = len(stages) - len(runnable)
        progress = f"{len(runnable)} steps will run" + (f" · {skipped} skipped" if skipped else "")

    with slot.container():
        st.markdown(
            _ANIM + f'<div style="display:flex;justify-content:space-between;'
            f'align-items:baseline;margin:0 0 0.9rem">'
            f'<span style="color:{MUTED};font-size:0.72rem;letter-spacing:0.08em">'
            f"THE PIPELINE</span>"
            f'<span style="color:{MUTED};font-size:0.78rem">{progress}</span></div>',
            unsafe_allow_html=True,
        )
        for i, stage in enumerate(stages):
            st.markdown(
                _row(stage, numbers.get(id(stage)), last=i == len(stages) - 1),
                unsafe_allow_html=True,
            )
            if stage.output and stage.state != "running":
                # Kept, but folded away: evidence you can go back to, without a
                # finished run being mostly log.
                with st.columns([1, 30])[1].expander(f"{stage.title} — output"):
                    st.code("\n".join(stage.output))


def _draw_live(stage: Stage | None, slot) -> None:
    """The running stage's output, redrawn as it arrives. Separate from the
    rail so the two can update at their own rates."""
    if stage is None:
        slot.empty()
        return
    with slot.container():
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;'
            f'align-items:baseline;margin:0.3rem 0 0.35rem">'
            f'<span style="color:{WHITE};font-size:0.82rem;font-weight:600">'
            f"▸ {stage.title}</span>"
            f'<span style="color:{YELLOW};font-size:0.78rem">'
            f"{_elapsed(stage.elapsed)}</span></div>",
            unsafe_allow_html=True,
        )
        st.code("\n".join(stage.output[-TAIL:]) or "starting...")


def _run_stage(stage: Stage, stages: list[Stage], rail, live, *, started: float) -> int:
    """Run one stage, ticking its row over from a number to a checkmark."""
    stage.state = "running"
    stage_started = time.time()
    _draw_rail(stages, rail, started=started)
    _draw_live(stage, live)

    proc = subprocess.Popen(
        stage.cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # Without this the child buffers when it is not writing to a terminal,
        # and nothing appears until it exits.
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdout is not None
    last_draw = 0.0
    for line in proc.stdout:
        stage.output.append(line.rstrip())
        stage.elapsed = time.time() - stage_started
        if stage.elapsed - last_draw >= REDRAW_EVERY_S:
            last_draw = stage.elapsed
            _draw_live(stage, live)
    code = proc.wait()

    stage.elapsed = time.time() - stage_started
    stage.state = "done" if code == 0 else "failed"
    _draw_rail(stages, rail, started=started)
    _draw_live(None, live)
    return code


def render() -> None:
    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Run")
    note(
        "An experiment runs as a pipeline: <b>provision</b>, <b>run</b>, "
        "<b>aggregate</b>, <b>report</b>, <b>tear down</b>. Every stage is one "
        "CLI command, so anything you see here you can rerun in a terminal."
    )

    # An experiment is a folder holding `experiment.yaml`, so its results can
    # live beside its config instead of in a separate tree.
    configs = (
        sorted(p for p in EXPERIMENTS.iterdir() if (p / "experiment.yaml").exists())
        if EXPERIMENTS.exists()
        else []
    )
    if not configs:
        empty_state(
            "No Experiments Yet",
            "Design one first — the Experiments page writes a config here.",
        )
        return

    names = [c.name for c in configs]
    last = st.session_state.get("last_config")
    default = names.index(Path(last).name) if last and Path(last).name in names else 0
    chosen = st.selectbox("Experiment", names, index=default)
    config_path = EXPERIMENTS / chosen
    cfg = _load(config_path)
    if cfg is None:
        return

    section("Where To Run It", "")
    target = st.radio(
        "Engine",
        ["Mock engine (free, no GPU)", "Already running (paste a URL)", "Provision a GPU"],
        help="The mock engine proves the wiring and costs nothing, but none of "
        "its timings mean anything about hardware. Real numbers need a real "
        "engine.",
    )

    engine_url: str | None = None
    provision = target.startswith("Provision")
    reuse = False
    teardown = True
    ready = True
    existing = _session_summary()

    if target.startswith("Mock"):
        port = int(st.number_input("Port", 1024, 65535, 8000))
        engine_url = f"http://127.0.0.1:{port}"
        ready = _mock_engine_running(port)
        if ready:
            st.success(f"Mock engine responding on port {port}.")
        else:
            warn(f"Nothing is serving on port {port}. Start it in a terminal first:")
            st.code(f"python scripts/mock_vllm.py --port {port} --capacity 4", language="bash")
    elif target.startswith("Already"):
        engine_url = st.text_input("Engine URL", "http://127.0.0.1:8000")
    else:
        i = cfg.infra
        note(
            f"Will deploy <b>{i.model}</b> on <b>{i.modal_gpu}</b> via {i.provider} "
            f"— max_num_seqs {i.engine.max_num_seqs}, tp "
            f"{i.engine.tensor_parallel_size}, prefix caching "
            f"{'on' if i.engine.enable_prefix_caching else 'off'}."
        )
        if existing:
            st.success(
                f"A session already exists: {existing.get('model')} on "
                f"{existing.get('gpu')}, created {existing.get('created_at')}."
            )
            reuse = st.checkbox(
                "Reuse it instead of deploying again",
                value=True,
                help="Bringing a model up takes minutes. Reuse only if this "
                "session was created from the same config — the engine settings "
                "are part of what a number means.",
            )
        teardown = st.checkbox(
            "Tear the deployment down when the pipeline finishes",
            value=True,
            help="A GPU left running keeps billing. Off only if you intend to "
            "run more experiments against the same deployment.",
        )

    c1, c2 = st.columns(2)
    out_name = c1.text_input("Results folder", config_path.name)
    repeats = int(c2.number_input("Override repeats (0 = use config)", 0, 10, 0))
    # Inside the experiment folder by default: one experiment, one place.
    out_dir = (
        config_path / "results"
        if out_name == config_path.name
        else RESULTS / (out_name or config_path.name)
    )

    stages = _plan(
        config_path=config_path,
        cfg=cfg,
        out_dir=out_dir,
        engine_url=engine_url,
        repeats=repeats,
        provision=provision,
        reuse=reuse,
        teardown=teardown,
    )

    st.markdown("<div style='height:0.6rem'></div>", unsafe_allow_html=True)
    # Two slots: the rail is the plan and the progress display, and the live
    # panel below it is the running stage's output. They redraw at different
    # rates, which is the only reason they are separate.
    rail = st.empty()
    live = st.empty()
    _draw_rail(stages, rail, started=None)

    if st.button("▸  Start", type="primary", disabled=not ready, width="stretch"):
        _execute(stages, rail, live, out_dir=out_dir)

    _recent()


def _execute(stages: list[Stage], rail, live, *, out_dir: Path) -> None:
    """Run the plan, stopping at the first failure — except teardown."""
    started = time.time()
    to_run = [s for s in stages if not s.skip]
    failure: Stage | None = None

    for stage in to_run:
        if failure is not None and not stage.always:
            # Not reached, and the rail should say so rather than leave it
            # looking like a step that is still coming.
            stage.state = "skipped"
            stage.skip = "the pipeline stopped before this"
            continue
        if _run_stage(stage, stages, rail, live, started=started) != 0:
            if stage.always:
                warn(f"<b>{stage.title} failed.</b> {stage.on_fail}")
            elif failure is None:
                failure = stage

    _draw_rail(stages, rail, started=started)

    if failure is not None:
        warn(f"<b>Stopped at “{failure.title}” after {_elapsed(time.time() - started)}.</b>")
        # What it actually said, before what it probably means. The tool knows
        # the real reason and the page was burying it in a collapsed expander
        # under a list of guesses — which is how "add a payment method to use
        # A10G" got read as a missing HF token.
        said = [line for line in failure.output if line.strip()][-12:]
        if said:
            st.caption("What it said")
            st.code("\n".join(said))
        note(failure.on_fail)
        return

    report = out_dir / "report.html"
    verdict(
        "Pipeline complete",
        f"{len(to_run)} stages in {_elapsed(time.time() - started)}. The report is at "
        f"<code>{report.relative_to(ROOT) if report.exists() else out_dir.relative_to(ROOT)}</code>; "
        "the Results page reads the same bundles interactively.",
    )
    if report.exists():
        st.download_button(
            "Download report.html",
            report.read_bytes(),
            file_name=f"{out_dir.name}-report.html",
            mime="text/html",
            width="stretch",
        )
        with st.expander("Preview the report"):
            from streamlit.components.v1 import html as embed

            embed(report.read_text(), height=900, scrolling=True)


def _recent() -> None:
    section("Recent Results", "")
    if not RESULTS.exists():
        st.markdown(
            f'<p style="color:{GREY};font-size:0.85rem">Nothing yet.</p>',
            unsafe_allow_html=True,
        )
        return

    recent = sorted(
        (d for d in RESULTS.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )[:6]
    for d in recent:
        runs = len(list(d.rglob("summary.json")))
        has_report = " · report" if (d / "report.html").exists() else ""
        st.markdown(
            f'<div style="background:{SURFACE};border:1px solid {LINE};'
            f'border-radius:4px;padding:0.6rem 0.9rem;margin-bottom:0.4rem">'
            f'<span style="color:{WHITE};font-weight:600">{d.name}</span>'
            f'<span style="color:{MUTED};font-size:0.82rem"> — {runs} runs{has_report}</span>'
            "</div>",
            unsafe_allow_html=True,
        )


render()
