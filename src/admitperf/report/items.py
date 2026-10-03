"""The seven reporting items, checked against the run rather than the author.

These are the things the survey found the corpus diverges on. Each one is either
recoverable from the run's own artifacts or it is not, and the report says which
without anyone deciding to be honest about it.

Item 3 is the one that matters. The other six are recoverable from most published
papers; item 3 is reported by almost nobody.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from admitperf.report.liveness import SignalRange, Verdict
from admitperf.report.taxonomy import PolicyCard


class ItemStatus(StrEnum):
    OK = "ok"
    FAIL = "FAIL"
    #: The run does not carry what this item needs. Different from FAIL: the item
    #: is not violated, it is unevidenced, and conflating the two either flatters
    #: the run or slanders it.
    UNKNOWN = "n/a"


@dataclass(frozen=True)
class Item:
    number: int
    title: str
    status: ItemStatus
    detail: str


@dataclass(frozen=True)
class RunFacts:
    """What the report generator needs to know about a run.

    Deliberately small and all optional except the signal range. Anything absent
    is reported as unevidenced rather than assumed, so a sparse run produces an
    honest report instead of a flattering one.
    """

    signal: SignalRange
    card: PolicyCard
    decisions: int = 0
    rejects: int = 0
    defers: int = 0
    offered_rps: float | None = None
    capacity_rps: float | None = None
    deadline_ms: float | None = None
    unloaded_ttft_ms: float | None = None
    repeats: int = 0
    spread_p95_ms: float | None = None
    params_source: str | None = None
    metric_denominator: str | None = None
    config_sha: str | None = None
    commit: str | None = None


def evaluate(facts: RunFacts) -> list[Item]:
    """The seven items, in the survey's order."""
    items: list[Item] = []

    # 1 — load relative to capacity, not in absolute requests per second.
    # "15 rps" means nothing without knowing what the cluster could do.
    if facts.offered_rps is not None and facts.capacity_rps:
        frac = facts.offered_rps / facts.capacity_rps
        items.append(
            Item(
                1,
                "Capacity-relative load",
                ItemStatus.OK,
                f"{facts.offered_rps:.3g} rps at {frac:.0%} of measured ceiling "
                f"({facts.capacity_rps:.3g} rps)",
            )
        )
    else:
        items.append(
            Item(
                1,
                "Capacity-relative load",
                ItemStatus.UNKNOWN,
                "offered load or measured ceiling not recorded; absolute rps is not "
                "comparable across deployments",
            )
        )

    # 2 — a deadline is only meaningful as a multiple of what the system does
    # when idle. 500 ms is generous on one cluster and impossible on another.
    if facts.deadline_ms is not None and facts.unloaded_ttft_ms:
        items.append(
            Item(
                2,
                "Calibrated deadlines",
                ItemStatus.OK,
                f"{facts.deadline_ms:.0f} ms = "
                f"{facts.deadline_ms / facts.unloaded_ttft_ms:.1f}x unloaded TTFT "
                f"({facts.unloaded_ttft_ms:.0f} ms)",
            )
        )
    else:
        items.append(
            Item(
                2,
                "Calibrated deadlines",
                ItemStatus.UNKNOWN,
                "no unloaded baseline recorded, so the deadline cannot be expressed "
                "as a multiple of idle latency",
            )
        )

    # 3 — the reason this report exists.
    verdict = facts.signal.verdict
    status = {
        Verdict.LIVE: ItemStatus.OK,
        Verdict.MARGINAL: ItemStatus.FAIL,
        Verdict.INERT: ItemStatus.FAIL,
        Verdict.UNKNOWN: ItemStatus.UNKNOWN,
    }[verdict]
    items.append(Item(3, "Signal liveness", status, facts.signal.explain()))

    # 4 — one run has no error bar, and a gap inside noise is not a gap.
    if facts.repeats >= 2 and facts.spread_p95_ms is not None:
        items.append(
            Item(
                4,
                "Repeats and spread",
                ItemStatus.OK,
                f"{facts.repeats} repeats, p95 spread +/-{facts.spread_p95_ms:.0f} ms",
            )
        )
    elif facts.repeats >= 2:
        items.append(
            Item(
                4,
                "Repeats and spread",
                ItemStatus.UNKNOWN,
                f"{facts.repeats} repeats but no spread computed",
            )
        )
    else:
        items.append(
            Item(
                4,
                "Repeats and spread",
                ItemStatus.FAIL,
                f"{max(facts.repeats, 1)} run; a single run has no error bar, so any "
                "difference from another run may be noise",
            )
        )

    # 5 — a threshold of 0.90 is a finding if it was tuned and an inherited
    # default if it was not. The distinction is invisible in a results table.
    if facts.params_source:
        items.append(
            Item(
                5,
                "Parameter provenance",
                ItemStatus.OK,
                f"threshold={facts.signal.threshold!s} ({facts.params_source})",
            )
        )
    else:
        items.append(
            Item(
                5,
                "Parameter provenance",
                ItemStatus.UNKNOWN,
                "not recorded whether the threshold was tuned, inherited, or guessed",
            )
        )

    # 6 — admission rate over *offered* and over *admitted* are different
    # numbers, and papers report both under the same name.
    if facts.metric_denominator:
        items.append(
            Item(
                6,
                "Metric definition",
                ItemStatus.OK,
                f"rates computed over {facts.metric_denominator}",
            )
        )
    else:
        items.append(
            Item(
                6,
                "Metric definition",
                ItemStatus.UNKNOWN,
                "denominator for admission and shed rates not stated",
            )
        )

    # 7 — a number traceable only to a version string that never changes is not
    # traceable.
    if facts.config_sha and facts.commit:
        items.append(
            Item(
                7,
                "Configuration disclosure",
                ItemStatus.OK,
                f"config {facts.config_sha[:12]} at commit {facts.commit[:12]}",
            )
        )
    else:
        missing = ", ".join(
            n for n, v in (("config hash", facts.config_sha), ("commit", facts.commit)) if not v
        )
        items.append(
            Item(
                7,
                "Configuration disclosure",
                ItemStatus.UNKNOWN,
                f"missing {missing}; this run is not reproducible from the report alone",
            )
        )

    return items
