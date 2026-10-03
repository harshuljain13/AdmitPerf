"""Did the signal ever move?

This is the one question the survey found almost nobody answers. A policy that
refuses above 0.90, run where the signal never passed 0.40, produces the same
latency numbers as no policy at all, and a reader cannot tell the two apart.

So the verdict is computed from the run's own decision log rather than asserted,
and it is computed for every run whether or not anyone asked for it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class Verdict(StrEnum):
    """What the signal did, in the only three ways that matter."""

    #: The signal reached the threshold. The policy had the opportunity to act.
    LIVE = "LIVE"
    #: The signal got close but never crossed. The run is near the transition
    #: point, which is interesting, and the policy still never fired.
    MARGINAL = "MARGINAL"
    #: The signal never approached the threshold. Nothing this run says about
    #: the policy is evidence about the policy.
    INERT = "INERT"
    #: No signal was recorded. Either the policy declares none, or the engine
    #: never supplied it. Reported rather than silently treated as zero.
    UNKNOWN = "UNKNOWN"


#: Within this fraction of the threshold counts as MARGINAL rather than INERT.
#: A run that reached 0.88 against a 0.90 threshold did not fire, but it is a
#: different kind of not-firing from one that peaked at 0.004.
MARGINAL_BAND = 0.10


@dataclass(frozen=True)
class SignalRange:
    """The distribution of a signal over one run.

    Percentiles rather than just min and max because the shape matters: a signal
    that touched the threshold once in ten thousand decisions is not the same as
    one that sat above it.
    """

    name: str
    threshold: float | None
    samples: int
    missing: int
    min: float
    p50: float
    p95: float
    p99: float
    max: float
    crossings: int
    #: The signal's shape over the run, rendered once at summarise time from the
    #: real series. Built here rather than at render time because the renderer
    #: only has the summary, and a sparkline drawn from min/p50/p95/max is a
    #: picture of the summary rather than of the run.
    spark: str = ""

    @property
    def verdict(self) -> Verdict:
        if self.samples == 0:
            return Verdict.UNKNOWN
        if self.threshold is None:
            # A signal was recorded but there is nothing to compare it against,
            # so we can describe it and must not judge it.
            return Verdict.UNKNOWN
        if self.max >= self.threshold:
            return Verdict.LIVE
        if self.threshold > 0 and self.max >= self.threshold * (1.0 - MARGINAL_BAND):
            return Verdict.MARGINAL
        return Verdict.INERT

    @property
    def headroom(self) -> float | None:
        """How far the peak fell short, as a fraction of the threshold.

        Negative once the threshold was crossed. This is the number to quote
        when explaining why a run proves nothing.
        """
        if self.threshold is None or self.threshold == 0:
            return None
        return (self.threshold - self.max) / self.threshold

    def explain(self) -> str:
        """One sentence a reader can act on."""
        if self.verdict is Verdict.UNKNOWN and self.samples == 0:
            return (
                f"no values of {self.name!r} were recorded, so whether the policy "
                "could have fired is unknown"
            )
        if self.threshold is None:
            return (
                f"{self.name} ranged {self.min:.4g} to {self.max:.4g}, but the policy "
                "declares no threshold, so there is nothing to compare it against"
            )
        if self.verdict is Verdict.LIVE:
            return (
                f"{self.name} reached {self.max:.4g} against a threshold of "
                f"{self.threshold:.4g}, crossing it {self.crossings} time(s) in "
                f"{self.samples} decisions"
            )
        pct = (self.headroom or 0.0) * 100
        tail = (
            "approached but never crossed"
            if self.verdict is Verdict.MARGINAL
            else "never approached"
        )
        return (
            f"{self.name} peaked at {self.max:.4g} against a threshold of "
            f"{self.threshold:.4g}, {pct:.0f}% short; it {tail} the threshold, so "
            "the policy could not have fired"
        )


def _percentile(ordered: list[float], q: float) -> float:
    """Nearest-rank percentile on an already-sorted list.

    Deliberately not interpolating. An interpolated p95 can report a value the
    signal never actually took, and this report's whole purpose is to say what
    the signal did.
    """
    if not ordered:
        return math.nan
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def summarise(values: list[float | None], *, name: str, threshold: float | None) -> SignalRange:
    """Reduce one run's recorded signal values to a reportable range.

    `None` entries are counted, not dropped quietly. A run where half the
    decisions had no signal is a broken run, and the count is how you notice.
    """
    present = sorted(v for v in values if v is not None)
    missing = len(values) - len(present)
    if not present:
        return SignalRange(
            name=name,
            threshold=threshold,
            samples=0,
            missing=missing,
            min=math.nan,
            p50=math.nan,
            p95=math.nan,
            p99=math.nan,
            max=math.nan,
            crossings=0,
            spark="(no signal recorded)",
        )
    crossings = sum(1 for v in present if v >= threshold) if threshold is not None else 0
    return SignalRange(
        name=name,
        threshold=threshold,
        samples=len(present),
        missing=missing,
        min=present[0],
        p50=_percentile(present, 0.50),
        p95=_percentile(present, 0.95),
        p99=_percentile(present, 0.99),
        max=present[-1],
        crossings=crossings,
        spark=sparkline([v for v in values if v is not None], threshold=threshold),
    )


#: Eight levels is enough to see shape and narrow enough to sit in a text report.
_BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(
    values: list[float | None], *, width: int = 48, threshold: float | None = None
) -> str:
    """A signal's shape over the run, in one line of text.

    Scaled against the threshold when there is one, so a flat line near the
    bottom reads as "nowhere near" rather than being stretched to fill the row.
    A sparkline normalised to its own max would make an inert run look busy,
    which is the opposite of what this report is for.
    """
    present = [v for v in values if v is not None]
    if not present:
        return "(no signal recorded)"
    buckets: list[float] = []
    n = len(present)
    for i in range(min(width, n)):
        lo = i * n // min(width, n)
        hi = max(lo + 1, (i + 1) * n // min(width, n))
        chunk = present[lo:hi]
        buckets.append(max(chunk))
    # Scale against the threshold when there is one. Normalising to the series'
    # own maximum would stretch an inert run to fill the row, making it look like
    # the signal was working hard when it never left the floor.
    top = threshold if threshold else (max(buckets) or 1.0)
    return "".join(_BLOCKS[min(8, int(round(v / top * 8)))] for v in buckets)
