"""The CLI. Three verbs, and none of them is in a request path.

    admitperf demo                                  try it, no GPU needed
    admitperf watch http://host:8000/metrics --for 1h -o trace.jsonl
    admitperf report trace.jsonl
    admitperf compare baseline.jsonl with-policy.jsonl
    admitperf dashboard

`admitperf.core` never fetches anything, because a network round trip has no place
on an admission decision. These commands run out of band, so `watch` scraping a
metrics endpoint for you is a convenience rather than a contradiction.

Nothing here provisions, serves, or generates load. That is your infra's job.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from admitperf import __version__


def _seconds(text: str) -> float:
    """`90`, `30s`, `15m`, `2h` — because `--for 3600` is how you mean an hour and
    type a mistake."""
    units = {"s": 1, "m": 60, "h": 3600}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Standardized admission control for LLM inference."""


@main.command()
@click.argument("url")
@click.option("--for", "duration", default="60s", show_default=True, help="90s · 15m · 2h")
@click.option("--every", default=1.0, show_default=True, help="seconds between scrapes")
@click.option("-o", "--out", default="trace.jsonl", show_default=True)
def watch(url: str, duration: str, every: float, out: str) -> None:
    """Record what your cluster is doing. No code in your request path.

    \b
      admitperf watch http://localhost:8000/metrics --for 1h -o trace.jsonl

    Answers the question no surveyed paper answers — could a policy have fired
    here, and which signal actually moved — before you change any code.
    """
    from admitperf.watch import Watch

    seconds = _seconds(duration)
    click.echo(f"watching {url} every {every:g}s for {seconds:g}s -> {out}")
    w = Watch(url, out, interval=every).run(seconds)
    click.echo(f"{w.samples} samples, {w.failures} failed scrape(s)")
    if w.samples == 0:
        raise SystemExit(f"nothing was recorded. Is {url} reachable and serving Prometheus text?")
    click.echo(f"\n  admitperf report {out}")


@main.command()
@click.argument("log", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--check",
    is_flag=True,
    help="exit non-zero unless the log can support a claim about the policy",
)
def report(log: str, check: bool) -> None:
    """The finding from a log.

    \b
      admitperf report trace.jsonl
      admitperf report decisions.jsonl --check     # for CI
    """
    from admitperf.report import Report

    r = Report.from_log(log)
    click.echo(r.text())
    if check and r.verdict() != "LIVE":
        raise SystemExit(f"verdict is {r.verdict()}: this log cannot support a claim")


#: Policies replay can construct, by the name you type.
REPLAYABLE = {
    "kv_threshold": ("threshold", 0.90),
    "queue_depth": ("max_waiting", 32),
    "dual_gate": ("threshold", 0.90),
    "no_admission": (None, None),
}


@main.command()
@click.argument("trace", type=click.Path(exists=True, dir_okay=False))
@click.argument("policy_name", metavar="POLICY")
@click.argument("params", nargs=-1, metavar="[KEY=VALUE]...")
@click.option("--experiment", "-e", default=None, help="name for the replayed runs")
@click.option("-o", "--out", default="experiments", show_default=True)
@click.option(
    "--baseline/--no-baseline",
    default=True,
    show_default=True,
    help="also replay no_admission, so there is something to compare against",
)
def replay(
    trace: str,
    policy_name: str,
    params: tuple[str, ...],
    experiment: str | None,
    out: str,
    baseline: bool,
) -> None:
    """What a policy WOULD have done against a recorded trace.

    \b
      admitperf watch http://host:8000/metrics --for 10m -o trace.jsonl
      admitperf replay trace.jsonl kv_threshold threshold=0.90

    This is what makes `watch` worth running. A watch trace holds metric snapshots and
    no decisions, so a report of it says UNKNOWN and leaves signal liveness unevidenced
    — the answer is in the file with nothing to compare it against. Replaying supplies
    the threshold, and the verdict follows. No gateway, no integration, no second run.

    A policy is a pure function of metrics, so N policies can be judged on ONE trace
    with identical inputs. That removes run-to-run variance from the comparison, which
    no published admission-control comparison currently offers.
    """
    from admitperf.core.registry_names import build
    from admitperf.replay import Replay
    from admitperf.report import Report

    kv: dict[str, Any] = {}
    for item in params:
        if "=" not in item:
            raise SystemExit(f"expected KEY=VALUE, got {item!r}")
        key, _, raw = item.partition("=")
        try:
            kv[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            kv[key] = raw

    rep = Replay.from_log(trace)
    if not rep.samples:
        raise SystemExit(
            f"{trace} holds no samples with metrics in them. If every scrape failed, "
            "the trace records that — and a policy cannot be replayed against a reading "
            "that was never taken."
        )
    name = experiment or f"replay-{Path(trace).stem}"
    click.echo(f"{len(rep.samples)} samples" + (f", {rep.skipped} skipped" if rep.skipped else ""))

    todo = [(policy_name, kv)]
    if baseline and policy_name != "no_admission":
        todo.append(("no_admission", {}))

    for pname, pkv in todo:
        policy = build(pname, **pkv)
        log = rep.against(policy, out=Path(out) / name / pname, experiment=name, run="r1")
        r = Report.from_log(log)
        duty = rep.duty_cycle(policy)
        click.echo(f"\n{'=' * 68}\n  replayed {pname} {pkv or ''}\n{'=' * 68}")
        click.echo(r.text())
        if pname != "no_admission":
            click.echo(
                f"\n  It would have been refusing for {duty:.1%} of the window.\n"
                "  A duty cycle, not a refusal rate: samples are periodic and arrivals\n"
                "  are not, so the two differ by however bursty the traffic was."
            )

    click.echo(f"\n  admitperf compare --experiment {name}")
    click.echo("  admitperf dashboard")


@main.command("experiments")
def list_experiments() -> None:
    """Every experiment found under this directory, and its arms."""
    from admitperf.discover import experiments

    found = experiments()
    if not found:
        click.echo("no logs here. `admitperf demo --repeats 5` makes some.")
        return
    for name, exp in sorted(found.items()):
        click.echo(f"\n{name}   ({exp.runs} run(s))")
        if exp.notes:
            click.echo(f"  {exp.notes}")
        for runs in exp.policies.values():
            click.echo(f"    {runs.label}")
        if exp.baseline is None:
            click.echo("    !! no baseline, so nothing can be compared against")


@main.command()
@click.option("--repeats", default=1, show_default=True, help="runs per policy")
@click.option(
    "-o",
    "--out",
    default="experiments",
    show_default=True,
    help="logs land in <out>/<experiment>/<policy>/r<n>.jsonl",
)
def demo(repeats: int, out: str) -> None:
    """Try the whole thing in one command. No GPU, no cluster, no config.

    \b
      admitperf demo
      admitperf demo --repeats 5

    Drives identical traffic through a simulated engine three times — admitting
    everything, with a KV-pressure threshold, and with a queue bound derived from the
    SLO — then compares the reports.

    A demo, not an experiment runner: it takes no engine URL and no workload, because
    AdmitPerf does not generate load. Against a real cluster your own load generator
    drives traffic and `admitperf watch` records it.
    """
    from admitperf.demo import main as run_demo

    run_demo(repeats=repeats, out=out, echo=click.echo)


@main.command()
@click.argument("baseline", type=click.Path(exists=True), required=False)
@click.argument("policy", type=click.Path(exists=True), required=False)
@click.option(
    "--experiment",
    "-e",
    help="compare every policy in a named experiment against its baseline",
)
@click.option(
    "--check",
    is_flag=True,
    help="exit non-zero unless the comparison is trustworthy: the policy fired, both "
    "runs faced the same load, and outcomes were recorded",
)
def compare(baseline: str | None, policy: str | None, experiment: str | None, check: bool) -> None:
    """What did the policy buy?

    \b
      admitperf compare --experiment demo-overload-2.5x   # every arm vs its baseline
      admitperf compare baseline.jsonl with-policy.jsonl  # or two paths directly

    The question the package exists to answer. Four checks come before any number,
    because a comparison between a policy that never fired and a baseline is two
    measurements of the same configuration.
    """
    from admitperf.comparison import Comparison
    from admitperf.discover import find
    from admitperf.report import Report

    if experiment:
        exp = find(experiment)
        if exp.baseline is None:
            raise SystemExit(
                f"experiment {experiment!r} has no baseline, so there is nothing to "
                "compare against. Run a policy with `baseline = True` — NoAdmission is one."
            )
        if not exp.candidates:
            raise SystemExit(f"experiment {experiment!r} has only a baseline")
        if exp.notes:
            click.echo(f"{experiment} — {exp.notes}\n")
        failed = False
        base = [Report.from_log(p) for p in exp.baseline.logs]
        for candidate in exp.candidates:
            click.echo(f"\n### {exp.baseline.label}  vs  {candidate.label}")
            c = Comparison(base, [Report.from_log(p) for p in candidate.logs])
            click.echo(c.text())
            failed = failed or not (c.fired() and c.comparable_load() and c.measurable())
        if check and failed:
            raise SystemExit("at least one pair cannot support a claim")
        return

    if not (baseline and policy):
        raise SystemExit("give two logs, or --experiment NAME. `admitperf dashboard` lists them.")
    c = Comparison.from_logs(baseline, policy)
    click.echo(c.text())
    if check and not (c.fired() and c.comparable_load() and c.measurable()):
        raise SystemExit("this pair cannot support a claim — see CAN YOU TRUST THIS above")


def _free_port(start: int, tries: int = 20) -> int:
    """The first free port at or above `start`.

    Bound on all interfaces and WITHOUT SO_REUSEADDR, because that is what Streamlit
    does. A probe against 127.0.0.1 with SO_REUSEADDR set reports a port as free while
    Streamlit then fails to bind it — which is exactly how "Port 8501 is not available"
    became a dead end instead of a retry.
    """
    import socket

    for offset in range(tries):
        candidate = start + offset
        with socket.socket() as s:
            try:
                s.bind(("", candidate))
            except OSError:
                continue
            return candidate
    raise SystemExit(f"no free port between {start} and {start + tries}")


@main.command()
@click.option("--port", default=8501, show_default=True, help="or the next one free")
def dashboard(port: int) -> None:
    """Open the dashboard: logs, findings, and what a policy bought.

    \b
      admitperf demo --repeats 5      # make some logs first
      admitperf dashboard

    It discovers logs under the current directory, so run it where your logs are.
    """
    import subprocess

    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit("the dashboard is an extra: pip install 'admitperf[dashboard]'") from None

    chosen = _free_port(port)
    if chosen != port:
        # A stale Streamlit from another checkout holding the default port should not
        # stop you looking at a report.
        click.echo(f"port {port} is busy, using {chosen}")
    click.echo(f"  http://localhost:{chosen}\n")
    app = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app),
            "--server.port",
            str(chosen),
            "--server.headless",
            "true",
        ],
        check=False,
    )


@main.command()
def policies() -> None:
    """The policies that ship with AdmitPerf."""
    from admitperf import policies as shipped

    for name in shipped.__all__:
        cls = getattr(shipped, name)
        doc = (cls.__doc__ or "").strip().splitlines()[0]
        click.echo(f"{cls.name:<16} {doc}")


@main.command()
def signals() -> None:
    """Every signal, and the metrics it reads.

    Run this first: it tells you what AdmitPerf can already read from your stack,
    and what to export (or map) for the rest.
    """
    from admitperf.core.signals import ALL

    for s in ALL:
        srcs = ", ".join(x if isinstance(x, str) else f"{x.__name__}()" for x in s.sources)
        rng = f"[{s.lo:g}, {s.hi:g}]" if s.hi is not None else f">= {s.lo:g}"
        click.echo(f"{s.name:<18} {rng:<12} {srcs}")
        click.echo(f"{'':<18} {s.help}")
        click.echo()
