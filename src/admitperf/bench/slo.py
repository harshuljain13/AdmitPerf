"""What counts as meeting an SLO, and what a rejected request counts as.

The second question is the one that decides whether a comparison means
anything, and getting it wrong is easy in a way that flatters shedding.

## Two attainment numbers, always reported together

    served   = met / admitted      how well it served what it took
    offered  = met / arrived       how well it served what was asked of it

A policy that refuses 95% of traffic and serves the rest perfectly scores 1.00
on served and 0.05 on offered. Quoting served alone makes aggressive shedding
look like a triumph, so `served` never appears in a summary without `offered`
beside it. Offered is the headline.

## Goodput is a rate, not a fraction

Published work reports goodput in requests per second — the load a system can
carry while still meeting its promises. Reporting a 0-1 fraction under the same
name invites a reader to compare it against numbers that mean something else.
So the fraction is called what it is, `offered_attainment`, and goodput is
`met / wall_seconds`.

Establishing the *sustainable* rate — the highest load at which attainment
stays above a target — needs a load sweep rather than a single run, which is
what `bench sweep` is for.

## A request passes only if both promises hold

Admission control has two jobs: get the first token out in time, and keep the
stream smooth afterwards. A request that starts promptly and then stalls
mid-answer has not been served well, so TTFT and inter-token latency are judged
jointly. Per-request p95 inter-token latency is used rather than the mean,
because a mean hides exactly the stalls that preemption causes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from admitperf.core.api import Request
from admitperf.core.ports import RequestOutcome


@dataclass(frozen=True)
class SLOVerdict:
    """Whether one request was served acceptably, and which promise failed."""

    judged: bool
    met: bool
    ttft_ok: bool | None
    itl_ok: bool | None
    ttft_ms: float | None
    itl_p95_ms: float | None

    @property
    def failed(self) -> str | None:
        if not self.judged or self.met:
            return None
        if self.ttft_ok is False:
            return "ttft"
        if self.itl_ok is False:
            return "itl"
        return "incomplete"


def percentile(values: tuple[float, ...] | list[float], q: float) -> float | None:
    """Nearest-rank percentile. None when there is nothing to rank."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    return ordered[min(len(ordered) - 1, max(0, rank - 1))]


def judge(req: Request, outcome: RequestOutcome) -> SLOVerdict:
    """Did this request meet what it was promised?

    A request that failed outright counts as judged and missed. Treating a
    failure as unjudged would quietly remove it from the denominator, which
    rewards a policy for admitting requests that then error.
    """
    itl_p95 = percentile(outcome.tbt_ms, 0.95)

    if outcome.status != "completed":
        return SLOVerdict(
            judged=True,
            met=False,
            ttft_ok=None,
            itl_ok=None,
            ttft_ms=outcome.ttft_ms,
            itl_p95_ms=itl_p95,
        )

    ttft_ok = (
        None
        if req.deadline_ttft_ms is None or outcome.ttft_ms is None
        else outcome.ttft_ms <= req.deadline_ttft_ms
    )
    itl_ok = (
        None if req.deadline_tbt_ms is None or itl_p95 is None else itl_p95 <= req.deadline_tbt_ms
    )

    if ttft_ok is None and itl_ok is None:
        # Nothing was promised, so nothing can be missed. Excluded from
        # attainment rather than counted as a free win.
        return SLOVerdict(False, False, None, None, outcome.ttft_ms, itl_p95)

    met = ttft_ok is not False and itl_ok is not False
    return SLOVerdict(True, met, ttft_ok, itl_ok, outcome.ttft_ms, itl_p95)


@dataclass
class Attainment:
    """SLO accounting across a run."""

    arrived: int = 0
    admitted: int = 0
    rejected: int = 0
    judged: int = 0
    met: int = 0
    missed_ttft: int = 0
    missed_itl: int = 0
    failed_outright: int = 0
    #: Output tokens spent on requests that missed anyway — the clearest
    #: statement of what a policy saved by refusing.
    wasted_output_tokens: int = 0
    useful_output_tokens: int = 0

    def add(self, verdict: SLOVerdict, outcome: RequestOutcome) -> None:
        if not verdict.judged:
            return
        self.judged += 1
        if verdict.met:
            self.met += 1
            self.useful_output_tokens += outcome.output_tokens
            return
        self.wasted_output_tokens += outcome.output_tokens
        if verdict.failed == "ttft":
            self.missed_ttft += 1
        elif verdict.failed == "itl":
            self.missed_itl += 1
        else:
            self.failed_outright += 1

    @property
    def served(self) -> float | None:
        """Of what it admitted. Flatters shedding; never report alone."""
        return self.met / self.judged if self.judged else None

    @property
    def offered(self) -> float | None:
        """Of everything that arrived. Rejections count as misses.

        The headline: a policy cannot improve this by refusing more, only by
        refusing *better*.
        """
        return self.met / self.arrived if self.arrived else None

    @property
    def wasted_fraction(self) -> float | None:
        """Share of generated tokens spent on requests that missed anyway."""
        total = self.wasted_output_tokens + self.useful_output_tokens
        return self.wasted_output_tokens / total if total else None


__all__ = ["Attainment", "SLOVerdict", "judge", "percentile"]
