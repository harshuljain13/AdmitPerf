"""Run every policy in a config against one deployment, and repeat.

The unit of measurement is the experiment, not the run. A live engine gives a
different number each time — cold caches, neighbour noise, scheduler jitter —
so one run per policy is a measurement, not a comparison. Repeats are what make
the difference between two policies interpretable.

All policies face the *same* deployment and the *same* seeded workload, so the
only thing that varies between them is the decision function. That is the whole
point, and it is why provisioning is a separate command: re-provisioning
between policies would change the thing being controlled for.
"""

from __future__ import annotations

import json
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from admitperf.bench.environment import engine_environment, local_environment
from admitperf.bench.results import summarize, write_bundle
from admitperf.bench.workloads.poisson import Baseline, PoissonWorkload
from admitperf.core.config import (
    Deployment,
    ExperimentConfig,
    PolicySpec,
    WorkloadConfig,
)
from admitperf.core.registry import get_policy
from admitperf.core.runner import Runner, RunnerConfig, RunResult
from admitperf.engines.vllm import VllmConfig, VllmEngine


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def run_one(
    cfg: ExperimentConfig,
    spec: PolicySpec,
    *,
    engine_url: str,
    served_model: str,
    seed: int,
    baseline: Baseline | None = None,
    workload_cfg: WorkloadConfig | None = None,
) -> tuple[dict[str, Any], RunResult]:
    """One policy, one repeat, under one traffic condition."""
    wl = workload_cfg or cfg.workload
    engine = VllmEngine(VllmConfig(base_url=engine_url, model=served_model))
    workload = PoissonWorkload(
        n_requests=wl.n,
        rate_per_s=wl.rate,
        seed=seed,
        duration_s=wl.duration_s,
        warmup_s=wl.warmup_s,
        baseline=baseline if wl.slo_mode == "relative" else None,
    )
    runner = Runner(
        workload=workload,
        policy=get_policy(spec.name, **spec.params),
        engine=engine,
        config=RunnerConfig(
            scrape_interval_s=cfg.bench.scrape_interval_s,
            max_state_age_s=cfg.bench.max_state_age_s,
            max_defers=cfg.bench.max_defers,
        ),
    )
    try:
        result = await runner.run()
    finally:
        await engine.aclose()
    return summarize(result, result.requests), result


def results_dir_for(config_path: str | Path | None, out: str | Path | None) -> Path | None:
    """Where a run should write, given how it was invoked.

    An explicit `--out` always wins. Otherwise, if the config came from an
    experiment folder, results belong in that folder: the point of the folder is
    that everything about one experiment is in one place, and a results
    directory somewhere else is how runs end up scattered.
    """
    if out is not None:
        return Path(out)
    if config_path is None:
        return None
    p = Path(config_path)
    folder = p if p.is_dir() else p.parent
    if (folder / ExperimentConfig.FILENAME).exists():
        return folder / "results"
    return None


class ResultsExistError(RuntimeError):
    """The target directory already holds runs."""


def guard_results_dir(root: Path, *, force: bool = False) -> None:
    """Refuse to write on top of an existing set of runs.

    A second run into the same directory silently replaced the first and
    destroyed the only copy of a result we had already reasoned about. Bundles
    are evidence; evidence does not get overwritten because a path was reused.
    """
    if force or not root.exists():
        return
    existing = sorted({p.parent.name for p in root.rglob("manifest.json")})
    if not existing:
        return
    raise ResultsExistError(
        f"{root} already holds {len(existing)} run(s) — for example "
        f"{existing[0]}. Writing here would replace them, and a bundle is the "
        "only record of what a number meant. Move or rename the old results, "
        "choose another --out, or pass --force if you genuinely want them gone."
    )


class ContextOverflowError(RuntimeError):
    """The workload can generate requests the engine cannot accept."""


def check_context_budget(cfg: ExperimentConfig, classes=None) -> None:
    """Refuse to start if the traffic cannot fit the engine's context window.

    vLLM answers an over-long request with a 400. The harness counts that as an
    admitted request that failed, which is indistinguishable in the summary
    from a request the engine dropped under load — so a third of a run can be
    configuration error wearing the costume of a result. Caught here it costs a
    second; caught afterwards it costs the whole deployment.
    """
    from admitperf.bench.workloads.poisson import DEFAULT_CLASSES

    limit = cfg.infra.engine.max_model_len
    if not limit:
        return

    offenders = [
        (c.name, c.input_tokens[1] + c.output_tokens[1])
        for c in (classes or DEFAULT_CLASSES)
        if c.input_tokens[1] + c.output_tokens[1] > limit
    ]
    if not offenders:
        return

    worst = max(n for _, n in offenders)
    detail = ", ".join(f"{name} up to {n}" for name, n in offenders)
    raise ContextOverflowError(
        f"this workload can generate requests of up to {worst} tokens "
        f"(prompt + output) but the engine is configured for "
        f"max_model_len={limit}: {detail}. Those requests come back as HTTP 400 "
        f"and are recorded as failures, which reads like overload rather than a "
        f"mismatch. Raise max_model_len to at least {worst}, or narrow the "
        f"workload classes."
    )


async def run_experiment(
    cfg: ExperimentConfig,
    *,
    engine_url: str,
    served_model: str,
    out_dir: Path | None = None,
    deployment: Deployment | None = None,
    baseline: Baseline | None = None,
) -> Path:
    """Every policy × every repeat against one deployment.

    One deployment, because that is the unit of comparison: policies are
    comparable only when they faced the same engine on the same hardware.
    Sweeping several deployments is `run_sweep`, which calls this once each.
    """
    check_context_budget(cfg)

    root = out_dir or Path("results") / f"{_stamp()}-{cfg.name}"
    root.mkdir(parents=True, exist_ok=True)

    print(
        f"experiment '{cfg.name}': {len(cfg.policies)} "
        f"{'policy' if len(cfg.policies) == 1 else 'policies'} × "
        f"{cfg.bench.repeats} repeat(s), "
        + (
            f"{cfg.workload.duration_s:.0f}s (warmup {cfg.workload.warmup_s:.0f}s)"
            if cfg.workload.duration_s
            else f"{cfg.workload.n} requests"
        )
        + f" at {cfg.workload.rate}/s against {engine_url}"
    )

    # Captured once: the engine does not change mid-experiment, and asking it
    # per run would add a round trip to every policy for the same answer.
    env = {
        "local": local_environment(),
        "engine": await engine_environment(engine_url),
    }
    engine_version = (env["engine"] or {}).get("version")
    print(f"  engine: vllm {engine_version or '(version not reported)'}")

    index: list[dict[str, Any]] = []
    situations = cfg.situations()
    if len(situations) > 1:
        print(
            f"  {len(situations)} situations against this one deployment: "
            + ", ".join(s.label for s in situations)
        )
    for situation in situations:
        wl = situation.workload
        if len(situations) > 1:
            print(f"\n  -- {situation.label} --")
        await _run_situation(
            cfg,
            situation.label,
            wl,
            root=root,
            engine_url=engine_url,
            served_model=served_model,
            baseline=baseline,
            deployment=deployment,
            env=env,
            index=index,
            single=len(situations) == 1,
        )

    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\nresults: {root}")
    print(f"compare: admitperf bench compare {root}")
    print(f"report:  admitperf bench report {root}")
    return root


async def _run_situation(
    cfg: ExperimentConfig,
    situation: str,
    wl: WorkloadConfig,
    *,
    root: Path,
    engine_url: str,
    served_model: str,
    baseline: Baseline | None,
    deployment: Deployment | None,
    env: dict[str, Any],
    index: list[dict[str, Any]],
    single: bool,
) -> None:
    """Every policy x every repeat, under one traffic condition."""
    for repeat in range(cfg.bench.repeats):
        # Shuffle within each repeat. A fixed order lets thermal drift, cache
        # warming and any slow leak accumulate against whichever policy always
        # runs last — it would look worse for reasons that are not its own.
        order = list(cfg.policies)
        if cfg.bench.randomize_policy_order:
            random.Random(wl.seed + repeat).shuffle(order)
        for spec in order:
            # Vary the seed per repeat, or every repeat replays identical
            # traffic and the spread measures nothing but engine jitter.
            seed = wl.seed + repeat
            label = f"{spec.label} [{repeat + 1}/{cfg.bench.repeats}]"
            print(f"  {label} ... ", end="", flush=True)

            summary, result = await run_one(
                cfg,
                spec,
                engine_url=engine_url,
                served_model=served_model,
                seed=seed,
                baseline=baseline,
                workload_cfg=wl,
            )
            print(
                f"admitted {summary['admitted']}/{summary['offered']}  "
                f"TTFT p95 {_ms(summary['ttft_ms']['p95'])}  "
                f"offered {summary['offered_attainment']}  "
                f"served {summary['served_attainment']}"
            )
            if not summary["signal_was_healthy"]:
                # Worth interrupting for. The run completes and the numbers
                # look ordinary, but the policy never saw the fleet, so it
                # cannot have done anything.
                print(
                    f"    WARNING: {result.scrape_failures} of "
                    f"{result.scrapes + result.scrape_failures} scrapes failed "
                    f"({summary['scrape_error']}). The policy was deciding on "
                    "stale state; this run does not measure it."
                )

            # Situation in the path, not just the manifest: a directory
            # listing should show what varied without opening anything.
            stem = f"{_slug(spec.label)}-r{repeat + 1}"
            run_dir = root / (stem if single else f"{_slug(situation)}/{stem}")
            write_bundle(
                run_dir,
                result=result,
                manifest={
                    "experiment": cfg.name,
                    "policy": spec.name,
                    "policy_params": spec.params,
                    # The signals this policy declared it needs, so a reader can
                    # check them against `signal_range` without knowing the code.
                    "policy_requires": sorted(
                        getattr(get_policy(spec.name, **spec.params), "requires", frozenset())
                    ),
                    "policy_label": spec.label,
                    "repeat": repeat + 1,
                    "repeats": cfg.bench.repeats,
                    "seed": seed,
                    "slo_mode": wl.slo_mode,
                    # The second axis of the comparison. Policies are
                    # comparable within a situation, never across two.
                    "situation": situation,
                    "offered_rate": wl.rate,
                    "workload": asdict(wl),
                    # The measured serveable rate this load is expressed
                    # against, when `bench capacity` has established one.
                    "capacity_ref": cfg.bench.capacity_ref,
                    "baseline": asdict(baseline) if baseline else None,
                    "engine_url": engine_url,
                    "deployment": deployment.label if deployment else None,
                    "deployment_infra": asdict(deployment.infra) if deployment else None,
                    "config": cfg.to_dict(),
                    "created_at": _stamp(),
                    # Which software produced this number. design.md promises
                    # it; without it a result cannot be reproduced or compared
                    # against a later one.
                    "environment": env,
                },
            )
            index.append(
                {
                    "policy": spec.label,
                    "deployment": deployment.label if deployment else None,
                    "situation": situation,
                    "offered_rate": wl.rate,
                    "repeat": repeat + 1,
                    # Relative, so a situation sweep and a single run index
                    # the same way.
                    "dir": str(run_dir.relative_to(root)),
                    **summary,
                }
            )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}ms"


async def run_sweep(
    cfg: ExperimentConfig,
    *,
    provision: Callable[[Deployment], Awaitable[tuple[str, str]]],
    teardown: Callable[[], Awaitable[None]],
    out_dir: Path | None = None,
) -> Path:
    """Provision each deployment in turn, run every policy against it, tear down.

    Results are filed one directory per deployment. Nothing pools across them:
    a table mixing an A10G row with an A100 row would be measuring hardware
    rather than admission control, so the comparison stays inside a deployment
    and the sweep only varies which deployment that is.

    Provisioning is injected rather than imported so this stays testable and so
    `bench run` against an engine you started yourself keeps working unchanged.
    """
    check_context_budget(cfg)

    root = out_dir or Path("results") / f"{_stamp()}-{cfg.name}"
    root.mkdir(parents=True, exist_ok=True)
    deployments = cfg.deployments()

    print(f"sweep '{cfg.name}': {len(deployments)} deployment(s) x {len(cfg.policies)} policies")
    for i, dep in enumerate(deployments, 1):
        print(f"\n[{i}/{len(deployments)}] {dep.label}")
        engine_url, served = await provision(dep)
        try:
            await run_experiment(
                cfg,
                engine_url=engine_url,
                served_model=served,
                out_dir=root / dep.label,
                deployment=dep,
            )
        finally:
            # Always tear down, including on failure: a deployment left running
            # after a crashed sweep bills until someone notices.
            await teardown()

    print(f"\nsweep results: {root}")
    print(f"compare:       admitperf bench compare {root}")
    return root


__all__ = ["run_experiment", "run_one", "run_sweep"]
