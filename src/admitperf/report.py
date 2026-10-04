"""What a log says, in the order a reader needs it."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from admitperf.core.log import Log
from admitperf.core.reasons import REASONS

WIDTH = 68
RULE = "=" * WIDTH
THIN = "-" * WIDTH


class Report:
    """The finding from one log.

    Ordered by what a reader needs, not by what is easy to compute:

      1. Could the policy have fired?  The validity gate. If the signal never
         reached the threshold the policy refused nothing, so every number below is
         a measurement of two identical configurations. This is the question none of
         the sixteen surveyed papers answers.
      2. Which signals actually moved?  Every signal is in the log, not just the one
         the policy read, so a flat signal can be told apart from a quiet cluster —
         and you learn that queue depth hit 61 while KV pressure never budged.
      3. What did the policy do?  Refused share, by reason.
      4. What did it cost?  Only if outcomes were recorded; otherwise it says so
         rather than quoting a number it cannot support.

    Each section states plainly when it cannot be answered. Nothing is omitted for
    being unflattering.
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.decisions = [r for r in records if "verdict" in r]
        self.outcomes = [r for r in records if "outcome" in r]
        self.states = [r for r in records if "verdict" not in r and "outcome" not in r]
        self.scrape_errors = sum(1 for r in records if "scrape_error" in r)

    @classmethod
    def from_log(cls, path: str | Path) -> Report:
        return cls(Log.read(path))

    # -- facts -------------------------------------------------------------

    @property
    def policy(self) -> str | None:
        return self.decisions[0]["policy"] if self.decisions else None

    @property
    def is_baseline(self) -> bool:
        """Whether the policy refuses nothing by design.

        A baseline and a policy that failed to fire produce identical counts, and
        they are opposite findings: one is the comparison arm working, the other is a
        run that proves nothing. Declared by the policy and recorded in the log,
        rather than guessed from the name.
        """
        return bool(self.decisions and self.decisions[0].get("baseline"))

    @property
    def params(self) -> dict[str, Any]:
        return self.decisions[0].get("params", {}) if self.decisions else {}

    @property
    def threshold(self) -> float | None:
        """The one parameter liveness is judged against, if the policy has one."""
        for key in ("threshold", "max_waiting", "min_hit_rate"):
            if key in self.params:
                return float(self.params[key])
        return None

    def signal_range(self, name: str) -> dict[str, float] | None:
        """min/p50/p95/max over every record that carried this signal.

        Percentiles are nearest-rank, not interpolated: an interpolated p95 can
        report a value the signal never took, which defeats the purpose of showing
        the range at all.
        """
        values = sorted(
            v for r in self.records if (v := (r.get("signals") or {}).get(name)) is not None
        )
        if not values:
            return None
        return {
            "min": values[0],
            "p50": values[min(len(values) - 1, len(values) // 2)],
            "p95": values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)],
            "max": values[-1],
            "samples": len(values),
        }

    def signals(self) -> dict[str, dict[str, float] | None]:
        names: list[str] = []
        for r in self.records:
            for name in r.get("signals") or {}:
                if name not in names:
                    names.append(name)
        return {name: self.signal_range(name) for name in names}

    @property
    def refused(self) -> int:
        return sum(1 for r in self.decisions if r["verdict"] != "admit")

    @property
    def enforced(self) -> int:
        return sum(1 for r in self.decisions if r.get("enforced"))

    def by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.decisions:
            if r.get("reason"):
                counts[r["reason"]] = counts.get(r["reason"], 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    @property
    def faults(self) -> int:
        return sum(1 for r in self.decisions if r.get("fault"))

    def verdict(self) -> str:
        """LIVE, INERT, UNKNOWN — whether this log can support a claim at all."""
        if not self.decisions:
            return "UNKNOWN"
        if self.refused:
            return "LIVE"
        return "INERT"

    # -- the page ----------------------------------------------------------

    def text(self) -> str:
        lines = [RULE, "  AdmitPerf", RULE, ""]
        lines += self.finding_text()
        lines += ["", THIN, "", "  WHICH SIGNALS MOVED", ""]
        lines += self.signals_text()
        lines += ["", THIN, "", "  WHAT IT COST", ""]
        lines += self.cost_text()
        if self.fault_text():
            lines += ["", THIN, "", "  THE INSTRUMENT", ""]
            lines += self.fault_text()
        lines.append(RULE)
        return "\n".join(lines)

    def finding_text(self) -> list[str]:
        v = self.verdict()
        if v == "UNKNOWN":
            return [
                f"  THE FINDING — UNKNOWN  ({len(self.states)} state samples)",
                "",
                "  No policy ran, so nothing was admitted or refused. This log says",
                "  what the cluster was doing, which is what you need to choose a",
                "  signal — see below — and not what a policy would have done.",
            ]
        total = len(self.decisions)
        if v == "INERT" and self.is_baseline:
            return [
                "  THE BASELINE — it admits everything",
                "",
                f"  {self.policy} admitted all {total} requests, which is its job. This is",
                "  what the cluster does unmanaged, and it is the arm every claim about",
                '  admission control is measured against: without it, "the policy refused',
                '  27%" has nothing to be 27% of.',
                "",
                "  The ranges below are the unmanaged cluster. Compare them against a",
                "  policy run:  admitperf compare <this log> <policy log>",
            ]
        if v == "INERT":
            return [
                "  THE FINDING — INERT",
                "",
                f"  {self.policy} refused nothing in {total} decisions.",
                "",
                "  This log cannot support a claim about the policy: its numbers are",
                "  indistinguishable from no policy at all. The fix is usually the",
                "  LOAD, not the policy — concurrency is arrival rate times request",
                "  duration, so short requests cannot fill a cache at any rate.",
                "  Check below whether a different signal moved.",
            ]
        share = self.refused / total
        reasons = ", ".join(f"{k} {n}" for k, n in self.by_reason().items())
        out = [
            "  THE FINDING — LIVE",
            "",
            f"  {self.policy} refused {self.refused} of {total} decisions ({share:.1%}).",
            f"  by reason: {reasons}",
        ]
        if self.enforced < total:
            out += [
                "",
                f"  {self.enforced} of {total} were actually enforced (enforce<1), so this",
                "  log holds both arms: refusals that happened and refusals that would",
                "  have. That is the comparison, under identical conditions.",
            ]
        return out

    def signals_text(self) -> list[str]:
        ranges = self.signals()
        if not ranges:
            return ["  nothing recorded"]
        thr = self.threshold
        out = [f"  {'signal':<18} {'min':>8} {'p50':>8} {'p95':>8} {'max':>8}   n"]
        for name, r in ranges.items():
            if r is None:
                out.append(f"  {name:<18} {'--':>8}   never supplied by your metrics")
                continue
            row = (
                f"  {name:<18} {r['min']:>8.3g} {r['p50']:>8.3g} "
                f"{r['p95']:>8.3g} {r['max']:>8.3g}   {r['samples']}"
            )
            out.append(row)
        # Both notes, independently. The absent explanation used to appear only when
        # the policy had no threshold, so the run most in need of it — a thresholded
        # policy whose signal was never supplied — was the one that never got it.
        if thr is not None:
            out += [
                "",
                f"  the policy's threshold was {thr:g}. A signal whose max never reached it",
                "  could not have made this policy fire, whatever else the numbers say.",
            ]
        if any(r is None for r in ranges.values()):
            out += [
                "",
                "  `--` is absent, not zero. A signal nothing supplies cannot be decided",
                "  on, and reading it as 0.0 would look like headroom.",
            ]
        return out

    def cost_text(self) -> list[str]:
        if not self.outcomes:
            return [
                "  not computed: no outcomes were recorded.",
                "",
                "  Without them this log says what the policy DID and never what it",
                "  BOUGHT — no latency, no goodput. Your gateway already measures",
                "  both; call policy.outcome(request_id, ttft_ms=..., ok=...) after",
                "  you forward a request.",
            ]
        ttfts = sorted(v for r in self.outcomes if (v := r["outcome"].get("ttft_ms")) is not None)
        ok = [r["outcome"].get("ok") for r in self.outcomes if "ok" in r["outcome"]]
        out = [f"  {len(self.outcomes)} outcomes recorded"]
        if ttfts:
            p95 = ttfts[min(len(ttfts) - 1, math.ceil(0.95 * len(ttfts)) - 1)]
            out.append(f"  TTFT of admitted   p50 {ttfts[len(ttfts) // 2]:.0f}ms   p95 {p95:.0f}ms")
        if ok and self.decisions:
            met = sum(1 for x in ok if x)
            # Definition 2: the denominator is everything OFFERED, so a refusal
            # counts as a miss. Dividing by admitted instead would reward a policy
            # for refusing more, rather than for refusing better.
            out.append(
                f"  goodput            {met / len(self.decisions):.3f}  "
                f"({met} met / {len(self.decisions)} offered)"
            )
        return out

    def fault_text(self) -> list[str]:
        out = []
        if self.scrape_errors:
            out.append(
                f"  {self.scrape_errors} scrape(s) failed — those intervals are missing "
                "from every range above, rather than recorded as an idle cluster."
            )
        if self.faults:
            out.append(
                f"  {self.faults} decision(s) hit a policy exception and failed open. "
                "Their verdicts are the fallback, not the policy's judgement."
            )
        bad = [r["reason"] for r in self.decisions if r.get("reason") not in {None, *REASONS}]
        if bad:
            out.append(f"  {len(bad)} refusal(s) used a reason outside the vocabulary: {set(bad)}")
        return out
