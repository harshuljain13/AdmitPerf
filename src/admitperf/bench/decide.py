"""Which policy to use, in which situation.

`bench compare` answers "what were the numbers" for one set of runs. This
answers the question those numbers exist to serve: *given traffic like this,
which admission policy should I run?* — with the situation named, because a
winner without one is an anecdote.

Three rules it will not break:

**A recommendation needs a baseline.** Without `no_admission` in the run there
is nothing to be better *than*, and "policy X scored highest" says nothing
about whether admitting everything would have done just as well.

**A margin inside the spread is not a margin.** Repeats exist to tell a real
difference from a noisy one. When the best policy's lead over the baseline is
smaller than the run-to-run range, the recommendation is "it does not matter",
which is a finding and not a failure.

**Situations are never pooled.** The whole point is that the answer changes
with load; averaging across load erases the only thing being measured.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from admitperf.bench.compare import PolicyStats

BASELINE = "no_admission"


@dataclass
class PolicyScore:
    label: str
    runs: int
    attainment: float | None
    spread: float
    admit_rate: float | None
    ttft_p95_ms: float | None
    unhealthy: int = 0


@dataclass
class Verdict:
    """What to run in one situation, and how much it is worth."""

    situation: str
    deployment: str
    offered_rate: float | None
    scores: list[PolicyScore]
    winner: str | None
    baseline: str | None
    #: Percentage points of offered attainment over the baseline.
    margin_pts: float
    #: Half the observed range, in the same units. Margins under this are noise.
    noise_pts: float
    recommendation: str
    caveats: list[str] = field(default_factory=list)

    @property
    def decisive(self) -> bool:
        return self.winner is not None and self.margin_pts > self.noise_pts


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _half_range(values: list[float]) -> float:
    return (max(values) - min(values)) / 2 if len(values) > 1 else 0.0


def _score(stats: PolicyStats) -> PolicyScore:
    rates = [a / o for a, o in zip(stats.admitted, stats.offered_count, strict=True) if o]
    return PolicyScore(
        label=stats.label,
        runs=stats.runs,
        attainment=_median(stats.offered),
        spread=_half_range(stats.offered),
        admit_rate=_median(rates),
        ttft_p95_ms=_median(stats.ttft_p95),
        unhealthy=stats.unhealthy,
    )


def _phrase(winner: PolicyScore, base: PolicyScore | None, margin: float, noise: float) -> str:
    """The recommendation, in the words someone would use to act on it."""
    if base is None:
        return (
            f"{winner.label} scored highest, but no {BASELINE} baseline was run, "
            "so there is nothing to say it beat."
        )
    if winner.label == base.label:
        return (
            f"Admit everything. No policy beat {BASELINE} here — refusing "
            "traffic cost attainment rather than protecting it."
        )
    if margin <= noise:
        return (
            f"It does not matter. {winner.label} leads {BASELINE} by "
            f"{margin:.1f} points, inside the +/-{noise:.1f} of run-to-run "
            "spread. Prefer the simpler policy."
        )
    return (
        f"Use {winner.label}. It meets {margin:.1f} more points of arriving "
        f"traffic than {BASELINE} ({winner.attainment:.1%} vs "
        f"{base.attainment:.1%}), admitting {winner.admit_rate:.0%} of requests."
    )


def _caveats(scores: list[PolicyScore]) -> list[str]:
    out = []
    degraded = sorted(s.label for s in scores if s.unhealthy)
    if degraded:
        out.append(
            f"{', '.join(degraded)} had runs where most metric scrapes failed; "
            "those numbers describe the workload, not the policy"
        )
    single = sorted(s.label for s in scores if s.runs == 1)
    if single:
        out.append(f"only one run for {', '.join(single)} — no spread to judge against")
    inert = sorted(
        s.label
        for s in scores
        if s.label != BASELINE and s.admit_rate is not None and s.admit_rate >= 0.999
    )
    if inert:
        out.append(
            f"{', '.join(inert)} admitted everything, so it was not exercised — "
            "its signal never crossed its threshold at this load"
        )
    return out


def decide(bundles: list[Any]) -> list[Verdict]:
    """One verdict per (deployment, situation), hardest load last.

    `bundles` are `report.Bundle`s — anything with `.manifest` and `.summary`.
    """
    groups: dict[tuple[str, str], list[Any]] = {}
    for b in bundles:
        key = (
            b.manifest.get("deployment") or "single deployment",
            b.manifest.get("situation") or "as configured",
        )
        groups.setdefault(key, []).append(b)

    verdicts: list[Verdict] = []
    for (deployment, situation), items in groups.items():
        stats: dict[str, PolicyStats] = {}
        for b in items:
            label = b.manifest.get("policy_label") or b.manifest.get("policy") or "unknown"
            stats.setdefault(label, PolicyStats(label)).add(b.summary)

        scores = sorted(
            (_score(s) for s in stats.values()),
            key=lambda s: (s.attainment is None, -(s.attainment or 0.0)),
        )
        scored = [s for s in scores if s.attainment is not None]
        base = next((s for s in scored if s.label.startswith(BASELINE)), None)
        winner = scored[0] if scored else None

        margin = (
            (winner.attainment - base.attainment) * 100
            if winner is not None and base is not None
            else 0.0
        )
        noise = max((winner.spread if winner else 0.0), (base.spread if base else 0.0)) * 100

        verdicts.append(
            Verdict(
                situation=situation,
                deployment=deployment,
                offered_rate=items[0].manifest.get("offered_rate"),
                scores=scores,
                winner=winner.label if winner else None,
                baseline=base.label if base else None,
                margin_pts=margin,
                noise_pts=noise,
                recommendation=(
                    _phrase(winner, base, margin, noise)
                    if winner
                    else "No attainment was recorded; nothing to recommend."
                ),
                caveats=_caveats(scores),
            )
        )

    verdicts.sort(key=lambda v: (v.deployment, v.offered_rate or 0.0))
    return verdicts


def render_text(verdicts: list[Verdict]) -> str:
    """The decision table, for a terminal."""
    if not verdicts:
        return "no runs to decide between"

    width = max(len(v.situation) for v in verdicts)
    lines = [
        "situation -> policy",
        "",
        f"{'situation':<{width}}  {'use':<28}  {'vs baseline':>12}  {'spread':>8}  verdict",
        f"{'-' * width}  {'-' * 28}  {'-' * 12}  {'-' * 8}  {'-' * 7}",
    ]
    for v in verdicts:
        use = v.winner or "-"
        lines.append(
            f"{v.situation:<{width}}  {use:<28}  {v.margin_pts:>+11.1f}p  "
            f"{v.noise_pts:>7.1f}p  {'decisive' if v.decisive else 'noise'}"
        )

    lines.append("")
    for v in verdicts:
        lines.append(f"{v.situation}: {v.recommendation}")
        for caveat in v.caveats:
            lines.append(f"    caveat: {caveat}")
    lines.append("")
    lines.append(
        "vs baseline is percentage points of offered attainment over "
        f"{BASELINE}; spread is half the observed range across repeats. A "
        "margin inside the spread is not a margin."
    )
    return "\n".join(lines)


__all__ = ["BASELINE", "PolicyScore", "Verdict", "decide", "render_text"]
