"""The CLI. Three verbs, and none of them is in a request path.

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


@main.command()
@click.argument("baseline", type=click.Path(exists=True, dir_okay=False))
@click.argument("policy", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--check",
    is_flag=True,
    help="exit non-zero unless the comparison is trustworthy: the policy fired, both "
    "runs faced the same load, and outcomes were recorded",
)
def compare(baseline: str, policy: str, check: bool) -> None:
    """What did the policy buy? Two logs, side by side.

    \b
      admitperf compare baseline.jsonl with-policy.jsonl

    The question the package exists to answer. Three checks come before any number,
    because a comparison between a policy that never fired and a baseline is two
    measurements of the same configuration.
    """
    from admitperf.comparison import Comparison

    c = Comparison.from_logs(baseline, policy)
    click.echo(c.text())
    if check and not (c.fired() and c.comparable_load() and c.measurable()):
        raise SystemExit("this pair cannot support a claim — see CAN YOU TRUST THIS above")


@main.command()
@click.option("--port", default=8501, show_default=True)
def dashboard(port: int) -> None:
    """Open the dashboard: logs and their findings, in a browser."""
    import subprocess

    app = Path(__file__).parent / "dashboard" / "app.py"
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise SystemExit("the dashboard is an extra: pip install 'admitperf[dashboard]'") from None
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(port)],
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
