"""AdmitPerf CLI.

Two groups, matching the two jobs:

    admitperf infra  up | status | smoke | down     provision an engine
    admitperf bench  run | compare                  measure policies against it

They are separate because bringing a model up takes minutes and you will run
many policies against one deployment.

    admitperf infra up -c experiments/demo.yaml
    admitperf infra smoke
    admitperf bench run -c experiments/demo.yaml
    admitperf bench compare results/
    admitperf infra down

Every setting lives in the config file; flags override it for one-offs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from admitperf import __version__
from admitperf.core.config import ConfigError, ExperimentConfig
from admitperf.core.registry import available


def _load(config: str | None, **overrides: object) -> ExperimentConfig:
    base = ExperimentConfig.load(config) if config else ExperimentConfig()
    return base.with_overrides(**overrides)


def _resolve_endpoint(engine_url: str | None) -> tuple[str, str]:
    """Explicit URL, else the provisioned session."""
    from admitperf.infra.session import SessionStore

    if engine_url:
        return engine_url, "lab"
    try:
        session = SessionStore().load()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    return session.primary, session.served_model_name


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Benchmark-driven admission control for LLM inference."""


@main.command()
def policies() -> None:
    """List every policy, including ones from installed plugins."""
    registry = available()
    width = max((len(n) for n in registry), default=10)
    for name, cls in sorted(registry.items()):
        requires = ", ".join(sorted(getattr(cls, "requires", frozenset()))) or "-"
        click.echo(f"{name:<{width}}  requires: {requires}  ({cls.__module__})")


# ---------------------------------------------------------------------------
# infra — provisioning
# ---------------------------------------------------------------------------


@main.group()
def infra() -> None:
    """Provision, inspect and tear down a serving engine."""


@infra.command("up")
@click.option("-c", "--config", default=None, help="Experiment YAML")
@click.option("--provider", type=click.Choice(["modal"]), default=None)
@click.option("--model", default=None, help="HuggingFace model id")
@click.option("--gpu", default=None, help="GPU type, e.g. A10G, A100, H100")
@click.option("--gpu-count", type=int, default=None, help="GPUs to attach")
@click.option("--tensor-parallel-size", "-tp", type=int, default=None)
@click.option("--pipeline-parallel-size", "-pp", type=int, default=None)
@click.option(
    "--max-num-seqs",
    type=int,
    default=None,
    help="Engine concurrency cap. Small on purpose: it is the bottleneck that "
    "creates queueing, and without queueing every policy scores alike.",
)
@click.option("--max-model-len", type=int, default=None)
@click.option(
    "--enable-prefix-caching/--no-enable-prefix-caching",
    default=None,
    help="Off by default for benchmarking: with it on, KV pressure stops reflecting offered load.",
)
@click.option("--scheduling-policy", type=click.Choice(["fcfs", "priority"]), default=None)
@click.option(
    "--hf-secret",
    default=None,
    help="Name of a Modal secret holding HF_TOKEN, for gated weights. "
    "Setting HF_TOKEN in the environment works too.",
)
def infra_up(config: str | None, hf_secret: str | None, **overrides: object) -> None:
    """Start an engine and remember where it is."""
    import os

    from admitperf.infra.modal_provider import ModalProvider, ProvisionError
    from admitperf.infra.session import SessionStore

    if hf_secret:
        os.environ["ADMITPERF_HF_SECRET"] = hf_secret

    try:
        cfg = _load(config, **overrides)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    i = cfg.infra
    click.echo(f"deploying {i.model} on {i.modal_gpu} via {i.provider}")
    click.echo(
        f"  tp={i.engine.tensor_parallel_size} pp={i.engine.pipeline_parallel_size} "
        f"max_num_seqs={i.engine.max_num_seqs} "
        f"prefix_caching={i.engine.enable_prefix_caching} "
        f"scheduling={i.engine.scheduling_policy}"
    )
    click.echo("this takes a few minutes...")

    try:
        session = ModalProvider(cfg).up()
    except ProvisionError as exc:
        raise SystemExit(str(exc)) from exc

    path = SessionStore().save(session)
    click.echo(f"endpoint: {session.primary}")
    click.echo(f"session:  {path}")
    click.echo("next: admitperf infra smoke")


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
    engine = (s.config.get("infra") or {}).get("engine") or {}
    if engine:
        click.echo(
            f"config:   tp={engine.get('tensor_parallel_size')} "
            f"pp={engine.get('pipeline_parallel_size')} "
            f"max_num_seqs={engine.get('max_num_seqs')} "
            f"prefix_caching={engine.get('enable_prefix_caching')}"
        )


@infra.command("smoke")
@click.option("--engine-url", default=None, help="Override the session endpoint")
def infra_smoke(engine_url: str | None) -> None:
    """Check the engine serves /v1/models, /metrics and a completion."""
    from admitperf.core.api import Request
    from admitperf.engines.vllm import VllmConfig, VllmEngine

    url, served = _resolve_endpoint(engine_url)

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

    if asyncio.run(check()):
        raise SystemExit("smoke failed")
    click.echo("SMOKE PASS")


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
        ModalProvider(ExperimentConfig()).down(session)
    except ProvisionError as exc:
        raise SystemExit(str(exc)) from exc
    store.clear()
    click.echo(f"stopped {session.provider} deployment of {session.model}")


# ---------------------------------------------------------------------------
# bench — measurement
# ---------------------------------------------------------------------------


@main.group()
def bench() -> None:
    """Measure admission policies against a running engine."""


@bench.command("run")
@click.option("-c", "--config", default=None, help="Experiment YAML")
@click.option("--engine-url", default=None, help="Override the session endpoint")
@click.option("--policy", multiple=True, help="Policy name; repeatable")
@click.option("-n", type=int, default=None, help="Requests per run")
@click.option("--rate", type=float, default=None, help="Arrivals per second")
@click.option("--seed", type=int, default=None)
@click.option("--repeats", type=int, default=None, help="Runs per policy")
@click.option("--out", default=None, help="Output directory")
def bench_run(
    config: str | None, engine_url: str | None, out: str | None, **overrides: object
) -> None:
    """Drive load through each policy and record what it cost."""
    from admitperf.bench.experiment import run_experiment

    try:
        cfg = _load(config, **overrides)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    url, served = _resolve_endpoint(engine_url)

    try:
        asyncio.run(
            run_experiment(
                cfg,
                engine_url=url,
                served_model=served,
                out_dir=Path(out) if out else None,
            )
        )
    except KeyboardInterrupt:
        raise SystemExit("interrupted") from None


@bench.command("sweep")
@click.option("-c", "--config", required=True, help="Experiment YAML with a `matrix:` section")
@click.option("--out", default=None, help="Output directory")
@click.option("--keep-up", is_flag=True, help="Leave the last deployment running")
def bench_sweep(config: str, out: str | None, keep_up: bool) -> None:
    """Provision each deployment in the matrix and run every policy against it.

    Comparisons stay inside a deployment. The sweep varies the hardware or
    engine settings; it never pools results across them, because that would
    measure the machine rather than the policy.
    """
    import asyncio as _asyncio

    from admitperf.bench.experiment import run_sweep
    from admitperf.core.config import Deployment
    from admitperf.engines.vllm import VllmConfig, VllmEngine
    from admitperf.infra.modal_provider import ModalProvider, ProvisionError
    from admitperf.infra.session import Session, SessionStore

    try:
        cfg = ExperimentConfig.load(config)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    store = SessionStore()
    live: list[tuple[ModalProvider, Session]] = []

    async def provision(dep: Deployment) -> tuple[str, str]:
        entry = ExperimentConfig(name=cfg.name, infra=dep.infra)
        i = dep.infra
        click.echo(f"  deploying {i.model} on {i.modal_gpu} (seqs={i.engine.max_num_seqs})")
        try:
            session = ModalProvider(entry).up()
        except ProvisionError as exc:
            raise SystemExit(str(exc)) from exc
        store.save(session)
        live.clear()
        live.append((ModalProvider(entry), session))

        # A freshly deployed container is cold; the first request pays for the
        # weights. Wait for readiness here so the run does not record a cold
        # start as the policy's latency.
        engine = VllmEngine(VllmConfig(base_url=session.primary, model=session.served_model_name))
        try:
            for _ in range(120):
                if await engine.health():
                    break
                await _asyncio.sleep(5)
            else:
                raise SystemExit(f"engine never became ready at {session.primary}")
        finally:
            await engine.aclose()

        click.echo(f"  ready: {session.primary}")
        return session.primary, session.served_model_name

    async def teardown() -> None:
        if keep_up:
            click.echo("  leaving deployment up (--keep-up)")
            return
        if not live:
            return
        provider, session = live[0]
        try:
            provider.down(session)
            store.clear()
            click.echo("  torn down")
        except ProvisionError as exc:
            # Report loudly but do not abort the sweep: a later deployment can
            # still produce results, and a stranded app costs money either way.
            click.echo(f"  WARNING: teardown failed: {exc}")

    _asyncio.run(
        run_sweep(cfg, provision=provision, teardown=teardown, out_dir=Path(out) if out else None)
    )


@bench.command("compare")
@click.argument("path", default="results", required=False)
def bench_compare(path: str) -> None:
    """Compare every run under a directory, grouped by policy."""
    from admitperf.bench.compare import compare_dir

    text = compare_dir(Path(path))
    if text is None:
        raise SystemExit(f"no result bundles under {path}")
    click.echo(text)


if __name__ == "__main__":
    main()
