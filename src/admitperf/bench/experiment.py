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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from admitperf.bench.results import summarize, write_bundle
from admitperf.bench.workloads.poisson import PoissonWorkload
from admitperf.core.config import ExperimentConfig, PolicySpec
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
) -> tuple[dict[str, Any], RunResult]:
    """One policy, one repeat."""
    engine = VllmEngine(VllmConfig(base_url=engine_url, model=served_model))
    workload = PoissonWorkload(
        n_requests=cfg.workload.n,
        rate_per_s=cfg.workload.rate,
        seed=seed,
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
    return summarize(result), result


async def run_experiment(
    cfg: ExperimentConfig,
    *,
    engine_url: str,
    served_model: str,
    out_dir: Path | None = None,
) -> Path:
    """Every policy × every repeat, written as one experiment directory."""
    root = out_dir or Path("results") / f"{_stamp()}-{cfg.name}"
    root.mkdir(parents=True, exist_ok=True)

    print(
        f"experiment '{cfg.name}': {len(cfg.policies)} "
        f"{'policy' if len(cfg.policies) == 1 else 'policies'} × "
        f"{cfg.bench.repeats} repeat(s), {cfg.workload.n} requests at "
        f"{cfg.workload.rate}/s against {engine_url}"
    )

    index: list[dict[str, Any]] = []
    for spec in cfg.policies:
        for repeat in range(cfg.bench.repeats):
            # Vary the seed per repeat, or every repeat replays identical
            # traffic and the spread measures nothing but engine jitter.
            seed = cfg.workload.seed + repeat
            label = f"{spec.label} [{repeat + 1}/{cfg.bench.repeats}]"
            print(f"  {label} ... ", end="", flush=True)

            summary, result = await run_one(
                cfg, spec, engine_url=engine_url, served_model=served_model, seed=seed
            )
            print(
                f"admitted {summary['admitted']}/{summary['offered']}  "
                f"TTFT p95 {_ms(summary['ttft_ms']['p95'])}  "
                f"goodput {summary['goodput_under_admission']}"
            )

            run_dir = root / f"{_slug(spec.label)}-r{repeat + 1}"
            write_bundle(
                run_dir,
                result=result,
                manifest={
                    "experiment": cfg.name,
                    "policy": spec.name,
                    "policy_params": spec.params,
                    "policy_label": spec.label,
                    "repeat": repeat + 1,
                    "repeats": cfg.bench.repeats,
                    "seed": seed,
                    "engine_url": engine_url,
                    "config": cfg.to_dict(),
                    "created_at": _stamp(),
                },
            )
            index.append(
                {"policy": spec.label, "repeat": repeat + 1, "dir": run_dir.name, **summary}
            )

    (root / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\nresults: {root}")
    print(f"compare: admitperf bench compare {root}")
    return root


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in text)


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}ms"


__all__ = ["run_experiment", "run_one"]
