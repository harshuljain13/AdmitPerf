#!/usr/bin/env python3
"""How much of Chronos's behaviour is the cost model rather than the algorithm?

Replays Algorithm 1's admission test across a grid of cost-model constants and
reports the admit rate at each. This is an *analytical* sensitivity study, not a
run: arrivals are synthesized here, the system state is held fixed, and
telemetry fitting is off, so the constants stay exactly where they are set. What
it shows is how much of the policy's output is decided by two numbers a paper
reports once and nobody re-fits.

    python scripts/gamma_sensitivity.py --out ../survey/figures/gamma-cliff.png

alpha = per-token prefill cost (ms), gamma = fixed per-chunk overhead (ms).
The paper's values, 0.08 and 7.0, are roofline-derived for a 7B model on an
A100-80GB. We run a 0.5B on an A10G.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from admitperf.bench.workloads.poisson import DEFAULT_CLASSES  # noqa: E402
from admitperf.core.api import Request, SystemState  # noqa: E402
from admitperf.policies.chronos.policy import ChronosInspiredWCRT  # noqa: E402

#: Held fixed so the only thing varying is the cost model. A live engine would
#: also move these, which is exactly why this is analysis and not measurement.
FROZEN_STATE = SystemState(
    now=0.0,
    kv_used_fraction=0.05,
    running_requests=2,
    waiting_requests=0,
    running_agents=0,
    per_tenant_running={},
    per_tenant_admitted_recent={},
    engine_metrics={},
)


def admit_rate(*, alpha: float, gamma: float, rate: float, n: int, seed: int) -> float:
    rng = random.Random(seed)
    policy = ChronosInspiredWCRT(
        fit_from_telemetry=False,
        chunk_tokens=512,
        alpha_ms_per_token=alpha,
        gamma_ms=gamma,
    )
    t = 0.0
    for i in range(n):
        t += rng.expovariate(rate)
        cls = rng.choices(DEFAULT_CLASSES, weights=[c.weight for c in DEFAULT_CLASSES])[0]
        policy.decide(
            Request(
                request_id=str(i),
                tenant_id="t",
                arrival_time=t,
                input_tokens=rng.randint(*cls.input_tokens),
                expected_output_tokens=rng.randint(*cls.output_tokens),
                deadline_ttft_ms=cls.deadline_ttft_ms,
                deadline_tbt_ms=cls.deadline_tbt_ms,
            ),
            FROZEN_STATE,
        )
    counts = policy.counts
    total = sum(v for k, v in counts.items() if k != "uncalibrated")
    return counts["admitted"] / total if total else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="gamma-cliff.png")
    ap.add_argument("--rate", type=float, default=4.0)
    ap.add_argument("-n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    alphas = [0.005, 0.01, 0.02, 0.04, 0.06, 0.08, 0.10]
    gammas = [1.0, 4.0, 7.0]

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=160)
    for gamma, style in zip(gammas, ("-", "--", ":"), strict=True):
        ys = [
            admit_rate(alpha=a, gamma=gamma, rate=args.rate, n=args.n, seed=args.seed) * 100
            for a in alphas
        ]
        ax.plot(alphas, ys, style, marker="o", color="#0B0B0B", label=f"gamma = {gamma} ms")
        for a, y in zip(alphas, ys, strict=True):
            print(f"alpha={a:<6} gamma={gamma:<5} admit={y:5.1f}%")

    ax.axvline(0.08, color="#F5C518", linewidth=6, alpha=0.5, zorder=0)
    ax.annotate(
        "the paper's value\n(7B on an A100)",
        xy=(0.08, 50),
        xytext=(0.055, 62),
        fontsize=8,
        color="#555555",
    )
    ax.set_xlabel("alpha: per-token prefill cost (ms)")
    ax.set_ylabel("requests admitted (%)")
    ax.set_ylim(-4, 104)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor="white")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
