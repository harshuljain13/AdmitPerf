"""AdmitPerf CLI.

Two groups, matching the two jobs:

    admitperf infra  up | status | smoke | down     provision an engine
    admitperf bench  run | compare                  measure policies against it

They are separate because bringing a model up takes minutes and you will run
many policies against one deployment.

    admitperf infra up -c experiments/shedding-vs-tail-latency
    admitperf infra smoke
    admitperf bench run -c experiments/shedding-vs-tail-latency
    admitperf bench compare results/
    admitperf infra down

Every setting lives in the config file; flags override it for one-offs.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import click

from admitperf.bench.workloads.poisson import Baseline
from admitperf.core import __version__
from admitperf.core.config import ConfigError, ExperimentConfig
from admitperf.core.registry import available, get_policy


def _load(config: str | None, **overrides: object) -> ExperimentConfig:
    base = ExperimentConfig.load(config) if config else ExperimentConfig()
    return base.with_overrides(**overrides)


def _resolve_endpoint(engine_url: str | None) -> tuple[str, str]:
    """Explicit URL, else the provisioned session."""
    from admitperf.core.session import SessionStore

    if engine_url:
        return engine_url, "lab"
    try:
        session = SessionStore().load()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    return session.primary, session.served_model_name


def _baseline_for(cfg: ExperimentConfig, engine_url: str | None) -> Baseline | None:
    """The measured baseline relative SLOs scale from, if one is needed."""
    from admitperf.core.session import SessionStore

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

    Subprocesses inherit this process's environment, so a token that only exists
    in a file never reaches them. Sourcing the file by hand works at a shell and
    not at all from the dashboard, which launches these commands itself — so a
    token sitting in `.env` looked present and was not.

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


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Benchmark-driven admission control for LLM inference."""
    load_dotenv()


@main.command()
@click.option("--verbose", "-v", is_flag=True, help="show the full policy card")
def policies(verbose: bool) -> None:
    """List every policy, including ones from installed plugins."""
    registry = available()
    width = max((len(n) for n in registry), default=10)
    for name, cls in sorted(registry.items()):
        signal = cls.signal or "-"
        thr = "-" if cls.threshold is None else f"{float(cls.threshold):g}"
        # A policy with no signal cannot have its liveness reported, which is the
        # one thing this tool exists to report. Say so here rather than at the end
        # of a run that has already cost GPU time.
        flag = "" if cls.signal else "   (no signal: liveness cannot be reported)"
        click.echo(f"{name:<{width}}  signal: {signal:<22} threshold: {thr:<6}{flag}")
        if verbose:
            requires = ", ".join(sorted(getattr(cls, "requires", frozenset()))) or "-"
            click.echo(
                f"{'':<{width}}  {cls.unit}/{cls.setting}/{cls.objective}"
                f"  Class {cls.portability}  requires: {requires}"
            )
            click.echo(f"{'':<{width}}  {cls.__module__}")


# ---------------------------------------------------------------------------
# infra — provisioning
# ---------------------------------------------------------------------------


@main.group()
def infra() -> None:
    """Render a cluster config, and check a running engine."""


@infra.command("status")
def infra_status() -> None:
    """Show the current session, if there is one."""
    from admitperf.core.session import SessionStore

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
    nothing to wait for; a rented cluster has a configured startup budget and
    that is the honest number to use.
    """
    if engine_url:
        return 30.0
    from admitperf.core.session import SessionStore

    store = SessionStore()
    if not store.exists():
        return default
    infra = store.load().config.get("infra") or {}
    return float(infra.get("startup_timeout_s") or default)


async def wait_for_engine(
    engine, *, timeout_s: float, tick=None, initial_delay: float = 2.0
) -> bool:
    """Poll until the engine answers, or the startup budget runs out.

    A bring-up returns when the pod reports ready, which is minutes before the
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
    from admitperf.core.engine import VllmConfig, VllmEngine

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
                        "It may still be loading weights — the engine's own logs "
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
    from admitperf.core.engine import VllmConfig, VllmEngine
    from admitperf.core.session import SessionStore

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


@infra.command("render")
@click.argument("config", type=click.Path(exists=True, dir_okay=False))
@click.option("--host", help="only this host's manifests")
@click.option("--env", "as_env", is_flag=True, help="print the gateway environment")
@click.option("--plan", "as_plan", is_flag=True, help="print what lands where")
@click.option("-o", "--out", type=click.Path(dir_okay=False))
def infra_render(
    config: str, host: str | None, as_env: bool, as_plan: bool, out: str | None
) -> None:
    """Turn a cluster config into manifests, or show what it would deploy.

    \b
      admitperf infra render infra/config/single.yaml --plan
      admitperf infra render infra/config/pair.yaml --env
      admitperf infra render infra/config/pair.yaml --host gpu-1 | kubectl apply -f -
    """
    from infra.render import ConfigError as RenderError
    from infra.render import gateway_env, load, plan, render

    try:
        cfg = load(Path(config))
        if as_plan:
            click.echo(plan(cfg))
            return
        if as_env:
            for k, v in gateway_env(cfg).items():
                click.echo(f"export {k}={v}")
            return
        text = render(cfg, host=host)
    except RenderError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    if out:
        Path(out).write_text(text)
        click.echo(f"wrote {out}", err=True)
    else:
        click.echo(text)


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
@click.option(
    "--out",
    default=None,
    help="Where to write. Defaults to results/ inside the experiment folder.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Write even if the target already holds runs. They will be replaced.",
)
def bench_run(
    config: str | None,
    engine_url: str | None,
    out: str | None,
    force: bool,
    **overrides: object,
) -> None:
    """Drive load through each policy and record what it cost."""
    from admitperf.bench.experiment import (
        ResultsExistError,
        guard_results_dir,
        results_dir_for,
        run_experiment,
    )

    try:
        cfg = _load(config, **overrides)
    except ConfigError as exc:
        raise SystemExit(f"config error: {exc}") from exc

    out_dir = results_dir_for(config, out)
    if out_dir is not None:
        try:
            guard_results_dir(out_dir, force=force)
        except ResultsExistError as exc:
            raise SystemExit(str(exc)) from exc

    url, served = _resolve_endpoint(engine_url)
    baseline = _baseline_for(cfg, engine_url)

    try:
        asyncio.run(
            run_experiment(
                cfg,
                engine_url=url,
                served_model=served,
                out_dir=out_dir,
                baseline=baseline,
            )
        )
    except KeyboardInterrupt:
        raise SystemExit("interrupted") from None


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


# ---------------------------------------------------------------------------
# report — the artifact
# ---------------------------------------------------------------------------


@main.command()
@click.argument("decisions", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--policy",
    "policy_name",
    default=None,
    help="policy the run used. Required for a decision log; a report.json already "
    "carries its policy card.",
)
@click.option("--threshold", type=float, help="override the policy's default")
@click.option("--run-id", help="defaults to the decision log's filename")
@click.option("--cluster", help="e.g. '4x H100-80 - 2 prefill + 2 decode'")
@click.option("--model")
@click.option("--engine")
@click.option("--commit", help="also evidences reporting item 7")
@click.option("--config-sha", help="also evidences reporting item 7")
@click.option("--offered-rps", type=float)
@click.option("--capacity-rps", type=float, help="measured ceiling, for item 1")
@click.option("--repeats", type=int, default=1, show_default=True)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "text", "md", "html"]),
    default="text",
    show_default=True,
    help="json is the artifact; the rest render from it",
)
@click.option("--markdown", is_flag=True, help="deprecated alias for --format md")
@click.option(
    "--check",
    is_flag=True,
    help="exit non-zero if the run cannot support a claim about the policy",
)
@click.option("-o", "--out", type=click.Path(dir_okay=False), help="write to a file")
def report(
    decisions: str,
    policy_name: str,
    threshold: float | None,
    run_id: str | None,
    fmt: str,
    markdown: bool,
    check: bool,
    out: str | None,
    **facts: object,
) -> None:
    """Render the AdmitPerf Report, from a report.json or a decision log.

    \b
      admitperf report results/kv_threshold-r1/report.json
      admitperf report results/kv_threshold-r1/report.json --format html -o r.html
      admitperf report decisions.jsonl --policy kv_threshold

    A report.json renders directly, since it is the artifact and every format is a
    pure function of it. A decision log is re-derived, which needs --policy.

    DECISIONS is JSON Lines, one object per admission decision. The signal value
    is read by the policy's own signal name, falling back to `signal_value`, so a
    gateway can log one column whatever policy is loaded.

    Anything not supplied is reported as unevidenced rather than assumed. A sparse
    log still produces an honest page; it just has more `n/a` on it.
    """
    import json

    from admitperf.report import RunHeader, facts_from, problems, render_as, to_dict

    text = Path(decisions).read_text()

    # A report.json renders directly. It IS the artifact and every format is a pure
    # function of it, so re-deriving one just to view it would be a second path to
    # the same page — and two paths drift.
    if text.lstrip().startswith("{"):
        payload = json.loads(text)
        if "schema_version" in payload:
            from admitperf.report import from_dict

            facts, header = from_dict(payload)
            page = render_as(facts, header, fmt="md" if markdown else fmt)
            if out:
                Path(out).write_text(page + "\n")
                click.echo(f"wrote {out}", err=True)
            else:
                click.echo(page)
            if check:
                found = problems(payload)
                for line in found:
                    click.echo(f"  ! {line}", err=True)
                if found:
                    raise SystemExit(1)
            return

    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows:
        raise click.ClickException(f"{decisions} has no decisions in it")

    if not policy_name:
        raise click.ClickException(
            f"{decisions} is a decision log, so --policy is needed: the signal name "
            "and threshold come from the policy, and liveness cannot be judged "
            "without them. A report.json carries its own policy card and needs no "
            "flag."
        )

    kwargs: dict[str, object] = {}
    if threshold is not None:
        kwargs["threshold"] = threshold
    try:
        policy = get_policy(policy_name, **kwargs)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc

    if markdown:
        fmt = "md"

    # The header identifies the run; RunFacts carries what the reporting items are
    # evidenced by. `commit` belongs to BOTH — it names the run and it evidences
    # item 7 — so it is the one key deliberately passed to each.
    header_only = ("cluster", "model", "engine")
    supplied = {k: v for k, v in facts.items() if v is not None}
    head = {k: supplied.pop(k) for k in header_only if k in supplied}
    header = RunHeader(
        run_id=run_id or Path(decisions).stem,
        commit=supplied.get("commit"),  # type: ignore[arg-type]
        **head,  # type: ignore[arg-type]
    )
    run = facts_from(policy, rows, **supplied)
    page = render_as(run, header, fmt=fmt)

    if out:
        Path(out).write_text(page + "\n")
        click.echo(f"wrote {out}", err=True)
    else:
        click.echo(page)

    if check:
        # Exit code, not prose, so a CI step can gate on it. An empty list does
        # not mean the policy worked — only that the run could show whether it did.
        found = problems(to_dict(run, header))
        for line in found:
            click.echo(f"  ! {line}", err=True)
        if found:
            raise SystemExit(1)


# ---------------------------------------------------------------------------
# run — one experiment, end to end
# ---------------------------------------------------------------------------


@main.command()
@click.argument("experiment", type=click.Path(exists=True))
@click.option(
    "--mock",
    is_flag=True,
    help="start a fake engine locally instead of using a real one. No GPU.",
)
@click.option(
    "--capacity",
    type=int,
    default=8,
    show_default=True,
    help="concurrent sequences the mock pretends to hold before it saturates",
)
@click.option("--engine-url", help="a real engine, if you brought one up yourself")
@click.option("--repeats", type=int, help="override the config")
def run(
    experiment: str,
    mock: bool,
    capacity: int,
    engine_url: str | None,
    repeats: int | None,
) -> None:
    """Drive one experiment's load and write a report per arm.

    \b
      admitperf run experiments/signal-liveness --mock
      admitperf run experiments/signal-liveness --engine-url http://127.0.0.1:8080

    The experiment says what load to send. The cluster config it points at says
    what is deployed and which admission policy is active. Each arm writes
    decisions.jsonl and report.json into the experiment's own results/.
    """
    import subprocess
    import sys as _sys
    import time as _time

    from admitperf.bench.drive import (
        ExperimentError,
        load_experiment,
        run_arm,
        scrape,
        write_arm,
    )
    from admitperf.reports.paths import repo_root

    try:
        exp = load_experiment(experiment)
    except ExperimentError as exc:
        raise SystemExit(str(exc)) from exc

    if not mock and not engine_url:
        raise SystemExit(
            "no engine. Pass --mock for a fake one, or --engine-url for a real one.\n"
            "Nothing is provisioned for you: a cluster bring-up is an SSH sequence "
            "against rented machines, and this command will not pretend otherwise."
        )

    proc = None
    engine_label = engine_url or ""
    if mock:
        mock_py = repo_root() / "tests" / "mock_vllm.py"
        engine_url = "http://127.0.0.1:8099"
        engine_label = f"mock_vllm.py (capacity {capacity})"
        proc = subprocess.Popen(
            [_sys.executable, str(mock_py), "--port", "8099", "--capacity", str(capacity)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            if scrape(engine_url, timeout=0.5).ok:
                break
            _time.sleep(0.2)
        else:
            proc.terminate()
            raise SystemExit("the mock engine never became ready")

    n_repeats = repeats if repeats is not None else exp.repeats
    arms = [exp.policy_name]
    if exp.baseline and exp.baseline != exp.policy_name:
        # The baseline runs against the SAME engine, so the only difference is
        # whether the policy was consulted. Without it, "admitted 82%" has nothing
        # to be 82% of.
        arms.append(exp.baseline)

    out_root = exp.path.parent / "results"
    click.echo(f"{exp.name}: {exp.cluster_path.name}, engine {engine_label}")
    click.echo(f"  arms {arms}  x{n_repeats} repeat(s)")

    try:
        for repeat in range(1, n_repeats + 1):
            for policy_name in arms:
                arm = run_arm(exp, policy_name=policy_name, engine_url=engine_url, seed=repeat)
                out = write_arm(
                    exp,
                    arm,
                    policy_name=policy_name,
                    repeat=repeat,
                    engine=engine_label,
                    out_root=out_root,
                )
                payload = json.loads((out / "report.json").read_text())
                verdict = payload["liveness"]["verdict"]
                d = payload["decisions"]
                click.echo(
                    f"  {policy_name:<14} r{repeat}  {verdict:<8} "
                    f"{d['rejected']}/{d['total']} rejected  "
                    f"signal max {payload['signal']['max']}  -> {out.name}/"
                )
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)

    from admitperf.report.html import render_index

    found = sorted(out_root.glob("*/report.json"))
    reports = [(q.parent.name, json.loads(q.read_text())) for q in found]
    index = out_root / "index.html"
    index.write_text(render_index(reports, title=exp.name) + "\n")

    click.echo(f"\n{len(reports)} arm(s) in {out_root}/")
    click.echo(f"\n  open {index}")
    click.echo("\nor, for every experiment at once:")
    click.echo("  admitperf dashboard")


@main.command()
@click.option("--port", type=int, default=8501, show_default=True)
def dashboard(port: int) -> None:
    """Open the dashboard: every experiment's reports, in a browser.

    Moves to the next free port if the one asked for is taken. Streamlit's own
    behaviour is to print "Port 8501 is not available" and exit, which is a dead end
    when the thing holding the port is a dashboard you opened an hour ago.
    """
    import socket
    import subprocess
    import sys as _sys

    def free(candidate: int) -> bool:
        """Bind exactly as Streamlit will, or the answer is about a different socket.

        Two mistakes, both of which made this probe report a held port as free:
        SO_REUSEADDR lets a bind succeed on macOS while another process is
        listening, and binding 127.0.0.1 says nothing about 0.0.0.0, which is what
        Streamlit uses. The combination reported "free" for a port that was in use
        by a dashboard opened an hour earlier.
        """
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", candidate))  # noqa: S104 — matches Streamlit
            except OSError:
                return False
        return True

    chosen = next((c for c in range(port, port + 20) if free(c)), None)
    if chosen is None:
        raise SystemExit(f"no free port between {port} and {port + 19}")
    if chosen != port:
        click.echo(f"{port} is taken, using {chosen}", err=True)

    app = Path(__file__).resolve().parent / "reports" / "dashboard" / "app.py"
    raise SystemExit(
        subprocess.call(
            [
                _sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app),
                "--server.port",
                str(chosen),
            ]
        )
    )


if __name__ == "__main__":
    main()
