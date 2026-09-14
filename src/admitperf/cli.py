"""AdmitPerf CLI.

The loop this exists to support:

    admitperf infra up --model Qwen/Qwen3-0.6B    # start a real vLLM on a GPU
    admitperf smoke                                # is it actually serving?
    admitperf run --policy kv_threshold            # drive load through a policy
    admitperf run --policy no_admission            # again, for comparison
    admitperf infra down                           # stop paying for it

`infra up` and `run` are separate commands because loading a model takes
minutes and you will run several policies against one deployment.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import click

from admitperf import __version__
from admitperf.core.registry import available, get_policy
from admitperf.core.runner import Runner, RunnerConfig, RunResult


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Benchmark-driven admission control for LLM inference."""


# --- policies -------------------------------------------------------------


@main.command()
def policies() -> None:
    """List every policy, including ones from installed plugins."""
    registry = available()
    if not registry:
        click.echo("no policies registered")
        return
    width = max(len(n) for n in registry)
    for name, cls in sorted(registry.items()):
        requires = ", ".join(sorted(getattr(cls, "requires", frozenset()))) or "-"
        click.echo(f"{name:<{width}}  requires: {requires}  ({cls.__module__})")


# --- infra ----------------------------------------------------------------


@main.group()
def infra() -> None:
    """Provision and tear down a serving engine."""


@infra.command("up")
@click.option("--provider", type=click.Choice(["modal"]), default="modal")
@click.option("--model", default="Qwen/Qwen3-0.6B", help="HuggingFace model id")
@click.option("--gpu", default="A10G", help="GPU type to request")
@click.option(
    "--max-num-seqs",
    default=8,
    show_default=True,
    help="Engine concurrency cap. Small on purpose: this is the bottleneck "
    "that creates queueing, and without queueing every policy scores alike.",
)
def infra_up(provider: str, model: str, gpu: str, max_num_seqs: int) -> None:
    """Start an engine and remember where it is."""
    from admitperf.infra.modal_provider import ModalProvider, ModalSpec, ProvisionError
    from admitperf.infra.session import SessionStore

    spec = ModalSpec(model=model, gpu=gpu, max_num_seqs=max_num_seqs)
    click.echo(f"deploying {model} on {gpu} via {provider} (this takes a few minutes)...")
    try:
        session = ModalProvider(spec).up()
    except ProvisionError as exc:
        raise SystemExit(str(exc)) from exc

    path = SessionStore().save(session)
    click.echo(f"endpoint: {session.primary}")
    click.echo(f"session:  {path}")
    click.echo("next: admitperf smoke")


@infra.command("status")
def infra_status() -> None:
    """Show the current session, if there is one."""
    from admitperf.infra.session import SessionStore

    store = SessionStore()
    if not store.exists():
        click.echo("no session. Run `admitperf infra up`.")
        return
    s = store.load()
    click.echo(f"provider: {s.provider}\nengine:   {s.engine}\nmodel:    {s.model}")
    click.echo(f"gpu:      {s.gpu}\nendpoint: {s.primary}\ncreated:  {s.created_at}")


@infra.command("down")
def infra_down() -> None:
    """Stop the engine and forget the session."""
    from admitperf.infra.modal_provider import ModalProvider, ProvisionError
    from admitperf.infra.session import SessionStore

    store = SessionStore()
    if not store.exists():
        click.echo("no session to tear down")
        return
    session = store.load()
    try:
        ModalProvider().down(session)
    except ProvisionError as exc:
        raise SystemExit(str(exc)) from exc
    store.clear()
    click.echo(f"stopped {session.provider} deployment of {session.model}")


# --- smoke ----------------------------------------------------------------


@main.command()
@click.option("--engine-url", default=None, help="Override the session endpoint")
def smoke(engine_url: str | None) -> None:
    """Check the engine serves /v1/models, /metrics, and a completion."""
    from admitperf.engines.vllm import VllmConfig, VllmEngine
    from admitperf.infra.session import SessionStore

    url, served = engine_url, "lab"
    if url is None:
        try:
            session = SessionStore().load()
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        url, served = session.primary, session.served_model_name

    async def check() -> int:
        engine = VllmEngine(VllmConfig(base_url=url, model=served))
        failures = 0
        try:
            ok = await engine.health()
            click.echo(f"{'PASS' if ok else 'FAIL'}  /v1/models")
            failures += not ok

            try:
                state = await engine.fetch_state()
                has_kv = state.kv_used_fraction is not None
                click.echo(
                    f"{'PASS' if has_kv else 'FAIL'}  /metrics "
                    f"(kv={state.kv_used_fraction}, waiting={state.waiting_requests})"
                )
                failures += not has_kv
            except Exception as exc:  # noqa: BLE001 - report, do not traceback
                click.echo(f"FAIL  /metrics: {exc}")
                failures += 1

            from admitperf.core.api import Request

            outcome = await engine.submit(
                Request(
                    request_id="smoke",
                    tenant_id="smoke",
                    arrival_time=0.0,
                    input_tokens=16,
                    expected_output_tokens=8,
                )
            )
            good = outcome.status == "completed"
            detail = (
                f"ttft={outcome.ttft_ms:.0f}ms, {outcome.output_tokens} tokens"
                if good and outcome.ttft_ms is not None
                else str(outcome.error)
            )
            click.echo(f"{'PASS' if good else 'FAIL'}  completion ({detail})")
            failures += not good
        finally:
            await engine.aclose()
        return failures

    failures = asyncio.run(check())
    if failures:
        raise SystemExit(f"{failures} check(s) failed")
    click.echo("SMOKE PASS")


# --- run ------------------------------------------------------------------


@main.command()
@click.option("--policy", required=True, help="Policy name (see `admitperf policies`)")
@click.option("--engine-url", default=None, help="Override the session endpoint")
@click.option("-n", "--requests", "n_requests", default=200, show_default=True)
@click.option("--rate", default=10.0, show_default=True, help="Arrivals per second")
@click.option("--seed", default=0, show_default=True)
@click.option("--out", default=None, help="Results directory (default: results/<timestamp>)")
def run(
    policy: str,
    engine_url: str | None,
    n_requests: int,
    rate: float,
    seed: int,
    out: str | None,
) -> None:
    """Drive load through a policy against the running engine."""
    from admitperf.bench.results import summarize, write_bundle
    from admitperf.bench.workloads.poisson import PoissonWorkload
    from admitperf.engines.vllm import VllmConfig, VllmEngine
    from admitperf.infra.session import SessionStore

    url, served, session_info = engine_url, "lab", {}
    if url is None:
        try:
            session = SessionStore().load()
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        url, served = session.primary, session.served_model_name
        session_info = {"model": session.model, "gpu": session.gpu, "provider": session.provider}

    engine = VllmEngine(VllmConfig(base_url=url, model=served))
    workload = PoissonWorkload(n_requests=n_requests, rate_per_s=rate, seed=seed)
    runner = Runner(
        workload=workload,
        policy=get_policy(policy),
        engine=engine,
        config=RunnerConfig(),
    )

    click.echo(f"running {n_requests} requests at {rate}/s through '{policy}' against {url}")

    async def go() -> RunResult:
        try:
            return await runner.run()
        finally:
            await engine.aclose()

    result = asyncio.run(go())
    summary = summarize(result)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(out) if out else Path("results") / f"{stamp}-{policy}"
    manifest = {
        "policy": policy,
        "engine": engine.name,
        "engine_url": url,
        "kv_scale": engine.kv_scale,
        "workload": {"name": workload.name, "n": n_requests, "rate_per_s": rate, "seed": seed},
        "session": session_info,
        "created_at": stamp,
        "admitperf_version": __version__,
    }
    write_bundle(out_dir, result=result, manifest=manifest)

    click.echo("")
    click.echo(f"  offered            {summary['offered']}")
    click.echo(f"  admitted           {summary['admitted']}  ({summary['admit_rate']})")
    click.echo(f"  rejected           {summary['rejected']}  {summary['reject_reasons']}")
    click.echo(f"  completed          {summary['completed']}  failed {summary['failed']}")
    click.echo(f"  TTFT p50/p95/p99   {_fmt(summary['ttft_ms'])}")
    click.echo(f"  TBT  p50/p95/p99   {_fmt(summary['tbt_ms'])}")
    click.echo(f"  goodput            {summary['goodput_under_admission']}")
    click.echo("")
    click.echo(f"results: {out_dir}")


def _fmt(p: dict[str, float | None]) -> str:
    return " / ".join("-" if p[k] is None else f"{p[k]:.0f}ms" for k in ("p50", "p95", "p99"))


if __name__ == "__main__":
    main()
