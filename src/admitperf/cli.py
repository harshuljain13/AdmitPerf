"""AdmitPerf CLI.

Two groups, matching the two jobs:

    admitperf infra  up | status | smoke | down     provision an engine
    admitperf bench  run | compare                  measure policies against it

They are separate because bringing a model up takes minutes and you will run
many policies against one deployment.

    admitperf infra up -c experiments/shedding-vs-tail-latency.yaml
    admitperf infra smoke
    admitperf bench run -c experiments/shedding-vs-tail-latency.yaml
    admitperf bench compare results/
    admitperf infra down

Every setting lives in the config file; flags override it for one-offs.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import click

from admitperf import __version__
from admitperf.bench.workloads.poisson import Baseline
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


def _baseline_for(cfg: ExperimentConfig, engine_url: str | None) -> Baseline | None:
    """The measured baseline relative SLOs scale from, if one is needed."""
    from admitperf.infra.session import SessionStore

    if cfg.workload.slo_mode != "relative":
        return None

    store = SessionStore()
    saved = store.load().baseline if store.exists() else None
    if not saved:
        raise SystemExit(
            "slo_mode is 'relative' but no baseline has been measured. "
            "Run `admitperf infra calibrate` first, or set slo_mode: absolute."
        )
    return Baseline(
        ttft_p50_ms=saved["ttft_p50_ms"],
        itl_p50_ms=saved["itl_p50_ms"],
        samples=int(saved.get("samples", 0)),
    )


#: Keys that came from `.env` rather than the shell, so `infra up` can say
#: which credentials it is about to deploy with.
DOTENV_KEYS: list[str] = []


def load_dotenv(path: Path = Path(".env")) -> list[str]:
    """Read `.env` into the environment, without overriding what is already set.

    `modal deploy` inherits this process's environment, so a token that only
    exists in a file never reaches the container. Sourcing the file by hand
    works at a shell and not at all from the dashboard, which launches these
    commands itself — so a token sitting in `.env` looked present and was not.

    Anything already exported wins: an explicit `HF_TOKEN=... admitperf ...`
    should not be silently replaced by a stale file.
    """
    loaded: list[str] = []
    try:
        text = path.read_text()
    except OSError:
        return loaded

    for raw in text.splitlines():
        line = raw.strip().removeprefix("export ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    DOTENV_KEYS[:] = loaded
    return loaded


def _auth_note() -> str:
    """Which Modal identity this deploy will use.

    `MODAL_TOKEN_ID` silently outranks `~/.modal.toml`, so a token in `.env`
    can put you in a different workspace than `modal profile current` reports —
    and "add a payment method" for an account you know is funded is a long way
    to walk before suspecting that."""
    token = os.environ.get("MODAL_TOKEN_ID")
    if not token:
        return "auth: ~/.modal.toml"
    source = ".env" if "MODAL_TOKEN_ID" in DOTENV_KEYS else "environment"
    return f"auth: MODAL_TOKEN_ID from {source} ({token[:8]}...), overriding ~/.modal.toml"


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Benchmark-driven admission control for LLM inference."""
    load_dotenv()


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
    click.echo(f"  {_auth_note()}")
    click.echo("this takes a few minutes...")

    try:
        # Echoed as they arrive rather than at the end: a deploy that is
        # downloading weights and one that is wedged look identical otherwise.
        session = ModalProvider(cfg).up(on_line=lambda line: click.echo(f"  {line}"))
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


def _startup_budget(engine_url: str | None, default: float = 600.0) -> float:
    """How long this deployment said it needs to come up.

    An engine you started yourself is either up or it is not, so there is
    nothing to wait for; a Modal deployment has a configured startup budget and
    that is the honest number to use.
    """
    if engine_url:
        return 30.0
    from admitperf.infra.session import SessionStore

    store = SessionStore()
    if not store.exists():
        return default
    infra = store.load().config.get("infra") or {}
    return float(infra.get("startup_timeout_s") or default)


async def wait_for_engine(
    engine, *, timeout_s: float, tick=None, initial_delay: float = 2.0
) -> bool:
    """Poll until the engine answers, or the startup budget runs out.

    `infra up` returns when Modal prints the URL, which is minutes before the
    engine can serve: the container is created on the first request, and then
    has to load weights. Checking once and reporting FAIL describes the clock,
    not the deployment.
    """
    import time as _time

    deadline = _time.monotonic() + timeout_s
    delay = initial_delay
    while True:
        try:
            if await engine.health():
                return True
        except Exception:  # noqa: BLE001 - still starting, not yet an error
            pass
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            return False
        if tick is not None:
            tick(timeout_s - remaining)
        await asyncio.sleep(min(delay, remaining))
        delay = min(delay * 1.5, 15.0)


@infra.command("smoke")
@click.option("--engine-url", default=None, help="Override the session endpoint")
@click.option(
    "--wait/--no-wait",
    default=True,
    show_default=True,
    help="Wait for the engine to finish starting before checking it.",
)
@click.option(
    "--timeout",
    type=float,
    default=None,
    help="Seconds to wait for startup. Defaults to the session's startup_timeout_s.",
)
def infra_smoke(engine_url: str | None, wait: bool, timeout: float | None) -> None:
    """Check the engine serves /v1/models, /metrics and a completion."""
    from admitperf.core.api import Request
    from admitperf.engines.vllm import VllmConfig, VllmEngine

    url, served = _resolve_endpoint(engine_url)
    budget = timeout if timeout is not None else _startup_budget(engine_url)

    async def check() -> int:
        engine = VllmEngine(VllmConfig(base_url=url, model=served))
        failures = 0
        try:
            if wait:
                click.echo(f"waiting for {url} to answer (up to {budget:.0f}s)...")
                ready = await wait_for_engine(
                    engine,
                    timeout_s=budget,
                    tick=lambda spent: click.echo(f"  still starting... {spent:.0f}s"),
                )
                if not ready:
                    click.echo(
                        f"FAIL  engine did not answer within {budget:.0f}s. "
                        "It may still be loading weights — `modal app logs admitperf-vllm` "
                        "will say."
                    )
                    return 1

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


@infra.command("calibrate")
@click.option("--engine-url", default=None, help="Override the session endpoint")
@click.option("--samples", default=12, show_default=True)
def infra_calibrate(engine_url: str | None, samples: int) -> None:
    """Measure unloaded latency, so SLOs can be set relative to it.

    Fixed millisecond deadlines do not transfer between regimes — 500ms is
    generous for a small model and impossible for a large one — so a comparison
    across hardware needs deadlines expressed as multiples of what the engine
    does when nothing is queued.
    """
    from admitperf.bench.calibrate import calibrate
    from admitperf.engines.vllm import VllmConfig, VllmEngine
    from admitperf.infra.session import SessionStore

    url, served = _resolve_endpoint(engine_url)

    async def go() -> Baseline:
        engine = VllmEngine(VllmConfig(base_url=url, model=served))
        try:
            return await calibrate(engine, samples=samples)
        finally:
            await engine.aclose()

    click.echo(f"sending {samples} requests one at a time against {url}...")
    baseline = asyncio.run(go())

    click.echo(f"  unloaded TTFT p50 : {baseline.ttft_p50_ms:.0f}ms")
    click.echo(f"  unloaded ITL  p50 : {baseline.itl_p50_ms:.1f}ms")
    click.echo(f"  samples           : {baseline.samples}")

    store = SessionStore()
    if store.exists():
        session = store.load()
        session.baseline = {
            "ttft_p50_ms": baseline.ttft_p50_ms,
            "itl_p50_ms": baseline.itl_p50_ms,
            "samples": float(baseline.samples),
        }
        store.save(session)
        click.echo("saved to the session; `slo_mode: relative` will use it")
    else:
        click.echo("no session to save into — pass these as absolute deadlines instead")


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
    baseline = _baseline_for(cfg, engine_url)

    try:
        asyncio.run(
            run_experiment(
                cfg,
                engine_url=url,
                served_model=served,
                out_dir=Path(out) if out else None,
                baseline=baseline,
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
            session = ModalProvider(entry).up(on_line=lambda line: click.echo(f"    {line}"))
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


@bench.command("capacity")
@click.option("-c", "--config", default=None, help="Experiment YAML")
@click.option("--engine-url", default=None, help="Override the session endpoint")
@click.option(
    "--rates",
    default="2,4,6,8,12,16,24",
    show_default=True,
    help="Arrival rates to walk, lowest first. Stops at the first failure.",
)
@click.option("--target", default=0.9, show_default=True, help="Attainment that counts as served")
@click.option("--duration", default=30.0, show_default=True, help="Seconds per rung")
@click.option("--warmup", default=10.0, show_default=True, help="Warmup seconds per rung")
def bench_capacity(
    config: str | None,
    engine_url: str | None,
    rates: str,
    target: float,
    duration: float,
    warmup: float,
) -> None:
    """Measure what this deployment serves, so the load axis means something.

    Runs the no-admission baseline at each rate and finds where it stops
    meeting its SLOs. Print a `loads:` block for a policy comparison.
    """
    from admitperf.bench.capacity import measure, render_text

    try:
        cfg = _load(config)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    url, served = _resolve_endpoint(engine_url)
    ladder = [float(r) for r in rates.split(",") if r.strip()]
    baseline = _baseline_for(cfg, engine_url)

    click.echo(f"walking {len(ladder)} rates against {url}, {duration:.0f}s each")
    cap = asyncio.run(
        measure(
            cfg,
            engine_url=url,
            served_model=served,
            rates=ladder,
            target=target,
            duration_s=duration,
            warmup_s=warmup,
            baseline=baseline,
            echo=click.echo,
        )
    )
    click.echo("")
    click.echo(render_text(cap))


@bench.command("decide")
@click.argument("path", default="results", required=False)
def bench_decide(path: str) -> None:
    """Which policy to use, per situation, across the runs under a directory."""
    from admitperf.bench.decide import decide, render_text
    from admitperf.bench.report import load_bundles

    bundles = load_bundles(Path(path))
    if not bundles:
        raise SystemExit(f"no result bundles under {path}")
    click.echo(render_text(decide(bundles)))


@bench.command("report")
@click.argument("path", default="results", required=False)
@click.option("--out", default=None, help="Where to write it (default: alongside the runs)")
def bench_report(path: str, out: str | None) -> None:
    """Write a self-contained HTML report for the runs under a directory."""
    from admitperf.bench.report import write

    root = Path(path)
    written = write(root, out_dir=Path(out) if out else None)
    if written is None:
        raise SystemExit(f"no result bundles under {path}")
    click.echo(f"report:  {written}")
    click.echo(f"table:   {written.parent / 'compare.txt'}")
    figures = written.parent / "figures"
    if figures.exists():
        click.echo(f"figures: {figures}/")


if __name__ == "__main__":
    main()
