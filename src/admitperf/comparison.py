"""Two logs, side by side: what did the policy buy?"""

from __future__ import annotations

import math
from pathlib import Path

from admitperf.report import THIN, WIDTH, Report

RULE = "=" * WIDTH


class Comparison:
    """A baseline run against a policy run.

    This is the question the whole package exists to answer, and the one the survey
    found sixteen papers answering incomparably. It is also the easiest thing to get
    wrong, so three checks come before any number:

      1. **Did the policy fire?** If it refused nothing, the two runs are the same
         configuration measured twice and any difference between them is noise.
      2. **Did both runs face the same load?** A policy run against half the traffic
         will look wonderful. Offered counts are reported side by side so a mismatch
         is visible rather than buried.
      3. **Can cost be measured at all?** Without outcomes there is no latency and no
         goodput, so the comparison can only report what was refused — which it says
         rather than implying more.

    Goodput divides by requests OFFERED, never by admitted. Dividing by admitted
    rewards a policy for refusing more rather than for refusing better: refuse 95% of
    traffic, serve the rest perfectly, and the number reads 1.00.
    """

    def __init__(self, baseline: Report, policy: Report) -> None:
        self.baseline = baseline
        self.policy = policy

    @classmethod
    def from_logs(cls, baseline: str | Path, policy: str | Path) -> Comparison:
        return cls(Report.from_log(baseline), Report.from_log(policy))

    # -- the three checks --------------------------------------------------

    def fired(self) -> bool:
        return self.policy.refused > 0

    def comparable_load(self) -> bool:
        """Within 10%. Two runs offered materially different traffic are not a
        comparison, however similar the configuration."""
        a, b = len(self.baseline.decisions), len(self.policy.decisions)
        if not a or not b:
            return False
        return abs(a - b) / max(a, b) <= 0.10

    def measurable(self) -> bool:
        return bool(self.baseline.outcomes and self.policy.outcomes)

    # -- the numbers -------------------------------------------------------

    @staticmethod
    def _ttft(report: Report) -> dict[str, float] | None:
        """Latency of requests that were admitted and returned."""
        values = sorted(
            v for r in report.outcomes if (v := r["outcome"].get("ttft_ms")) is not None
        )
        if not values:
            return None
        return {
            "p50": values[len(values) // 2],
            "p95": values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)],
            "max": values[-1],
            "n": len(values),
        }

    @staticmethod
    def _goodput(report: Report) -> float | None:
        """Met-SLO over OFFERED. A refusal counts as a miss, which is the point."""
        offered = len(report.decisions)
        if not offered or not report.outcomes:
            return None
        met = sum(1 for r in report.outcomes if r["outcome"].get("ok"))
        return met / offered

    def refused_share(self) -> float:
        total = len(self.policy.decisions)
        return self.policy.refused / total if total else 0.0

    # -- the page ----------------------------------------------------------

    def text(self) -> str:
        lines = [RULE, "  AdmitPerf — what did the policy buy?", RULE, ""]
        lines += self._headline()
        lines += ["", THIN, "", "  SIDE BY SIDE", ""]
        lines += self._table()
        lines += ["", THIN, "", "  CAN YOU TRUST THIS?", ""]
        lines += self._trust()
        lines.append(RULE)
        return "\n".join(lines)

    def _headline(self) -> list[str]:
        if not self.fired():
            return [
                "  NO FINDING — the policy never fired.",
                "",
                f"  {self.policy.policy or 'the policy'} refused 0 of "
                f"{len(self.policy.decisions)} requests, so these two runs are the same",
                "  configuration measured twice. Any difference between the numbers below",
                "  is noise, and a latency improvement here would be a false result.",
                "",
                "  The fix is usually the LOAD, not the policy: concurrency is arrival",
                "  rate times request duration, so short requests cannot fill a cache at",
                "  any rate. `admitperf report` on the policy log shows which signal did",
                "  move, if any.",
            ]

        out = [
            f"  The policy refused {self.policy.refused} of {len(self.policy.decisions)} "
            f"requests ({self.refused_share():.1%}).",
            "",
        ]
        if not self.measurable():
            out += [
                "  What that bought cannot be measured: no outcomes were recorded, so",
                "  there is no latency and no goodput. Call policy.outcome(...) after you",
                "  forward a request and run this again.",
            ]
            return out

        base_t, pol_t = self._ttft(self.baseline), self._ttft(self.policy)
        base_g, pol_g = self._goodput(self.baseline), self._goodput(self.policy)
        if base_t and pol_t and base_t["p95"]:
            factor = base_t["p95"] / pol_t["p95"] if pol_t["p95"] else float("inf")
            direction = "lower" if factor > 1 else "HIGHER"
            out.append(
                f"  p95 TTFT of served requests is {abs(factor):.2f}x {direction}: "
                f"{base_t['p95']:.0f}ms -> {pol_t['p95']:.0f}ms"
            )
        if base_g is not None and pol_g is not None:
            verb = "up" if pol_g > base_g else "DOWN"
            out.append(f"  goodput is {verb}: {base_g:.3f} -> {pol_g:.3f}  (of offered)")
            if pol_g < base_g:
                out += [
                    "",
                    "  Goodput fell, so the policy refused requests the cluster could have",
                    "  served. Faster tails bought at that price are not a win.",
                ]
        return out

    def _table(self) -> list[str]:
        base_t, pol_t = self._ttft(self.baseline), self._ttft(self.policy)
        base_g, pol_g = self._goodput(self.baseline), self._goodput(self.policy)

        def cell(value: float | None, fmt: str = "{:.0f}") -> str:
            return "--" if value is None else fmt.format(value)

        rows = [
            ("policy", self.baseline.policy or "-", self.policy.policy or "-"),
            ("offered", str(len(self.baseline.decisions)), str(len(self.policy.decisions))),
            ("refused", str(self.baseline.refused), str(self.policy.refused)),
            (
                "p50 TTFT ms",
                cell(base_t and base_t["p50"]),
                cell(pol_t and pol_t["p50"]),
            ),
            (
                "p95 TTFT ms",
                cell(base_t and base_t["p95"]),
                cell(pol_t and pol_t["p95"]),
            ),
            ("goodput", cell(base_g, "{:.3f}"), cell(pol_g, "{:.3f}")),
        ]
        out = [f"  {'':<16} {'baseline':>14} {'with policy':>14}", ""]
        out += [f"  {label:<16} {a:>14} {b:>14}" for label, a, b in rows]

        # Signals, because "the policy changed the cluster" is checkable rather than
        # assumed: KV pressure that did not move means the refusals bought nothing.
        shared = [n for n in self.policy.signals() if n in self.baseline.signals()]
        if shared:
            out += ["", f"  {'signal max':<16} {'baseline':>14} {'with policy':>14}", ""]
            for name in shared:
                a = self.baseline.signal_range(name)
                b = self.policy.signal_range(name)
                out.append(
                    f"  {name:<16} {cell(a and a['max'], '{:.3g}'):>14} "
                    f"{cell(b and b['max'], '{:.3g}'):>14}"
                )
        return out

    def _trust(self) -> list[str]:
        out = []
        mark = {True: "ok ", False: "NO "}
        out.append(f"  {mark[self.fired()]} the policy fired at all")
        out.append(f"  {mark[self.comparable_load()]} both runs were offered the same load")
        if not self.comparable_load():
            out.append(
                f"      {len(self.baseline.decisions)} vs {len(self.policy.decisions)} "
                "requests — a policy run against less traffic will look better than it is"
            )
        out.append(f"  {mark[self.measurable()]} outcomes recorded, so cost is measurable")
        for name, r in (("baseline", self.baseline), ("policy", self.policy)):
            for fault in r.fault_text():
                out.append(f"  !! {name}:{fault.strip()}")
        if self.fired() and self.comparable_load() and self.measurable():
            out += [
                "",
                "  All three hold, so the difference above is the policy. Repeat the pair",
                "  before quoting it: one run of each has no error bar, and a gap smaller",
                "  than the spread between repeats is not a result.",
            ]
        return out
