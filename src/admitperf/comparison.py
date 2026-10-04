"""Two logs, side by side: what did the policy buy?"""

from __future__ import annotations

import math
from pathlib import Path

from admitperf.report import THIN, WIDTH, Report

RULE = "=" * WIDTH


def _reports(target: str | Path) -> list[Report]:
    """One log, or every log in a directory.

    A directory is how an arm gets repeats. Sorted by name so `r1, r2, r10` is at
    least stable between invocations, even though it is not numeric order — the
    comparison does not care about sequence, only that the same set is read twice.
    """
    path = Path(target)
    if path.is_dir():
        logs = sorted(path.glob("*.jsonl"))
        if not logs:
            raise ValueError(f"no .jsonl logs in {path}")
        return [Report.from_log(p) for p in logs]
    return [Report.from_log(path)]


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

    def __init__(
        self,
        baseline: Report | list[Report],
        policy: Report | list[Report],
    ) -> None:
        self.baselines = [baseline] if isinstance(baseline, Report) else list(baseline)
        self.policies = [policy] if isinstance(policy, Report) else list(policy)
        if not self.baselines or not self.policies:
            raise ValueError("a comparison needs at least one run on each side")
        #: The representative run of each arm, for the parts that do not vary between
        #: repeats: which policy, its parameters, whether it fired.
        self.baseline = self.baselines[0]
        self.policy = self.policies[0]

    @classmethod
    def from_logs(cls, baseline: str | Path, policy: str | Path) -> Comparison:
        """Each side is a log file, or a DIRECTORY of repeats of that arm.

        A directory is how you get an error bar. One run of each arm has none, and a
        gap smaller than the spread between repeats is not a result — so the tool has
        to be able to read more than one.
        """
        return cls(_reports(baseline), _reports(policy))

    # -- repeats -----------------------------------------------------------

    @property
    def repeats(self) -> int:
        """The smaller of the two, since that is what the comparison is limited by."""
        return min(len(self.baselines), len(self.policies))

    @staticmethod
    def _spread(values: list[float]) -> tuple[float, float, float]:
        """Median, min, max. Median rather than mean because one pathological repeat
        — a stalled scrape, a noisy neighbour — should not move the headline."""
        ordered = sorted(values)
        return ordered[len(ordered) // 2], ordered[0], ordered[-1]

    def _arm(self, reports: list[Report], metric) -> tuple[float, float, float] | None:
        values = [v for r in reports if (v := metric(r)) is not None]
        return self._spread(values) if values else None

    def separated(self) -> bool | None:
        """Whether the two arms are further apart than their own repeats are.

        None when there is only one run per arm, because then the question cannot be
        asked — and answering it anyway is how a difference inside the noise gets
        published as a finding.
        """
        if self.repeats < 2:
            return None
        base = self._arm(self.baselines, self._goodput)
        pol = self._arm(self.policies, self._goodput)
        if base is None or pol is None:
            return None
        # No overlap between the two arms' observed ranges.
        return base[2] < pol[1] or pol[2] < base[1]

    # -- the three checks --------------------------------------------------

    def fired(self) -> bool:
        """Every repeat must have fired. One that did not is a different experiment,
        and averaging it in hides that."""
        return all(r.refused > 0 for r in self.policies)

    def comparable_load(self) -> bool:
        """Within 10%, across every run on both sides. Two runs offered materially
        different traffic are not a comparison, however similar the configuration."""
        counts = [len(r.decisions) for r in self.baselines + self.policies]
        if not all(counts):
            return False
        return (max(counts) - min(counts)) / max(counts) <= 0.10

    def measurable(self) -> bool:
        return all(r.outcomes for r in self.baselines + self.policies)

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
        shares = [r.refused / len(r.decisions) for r in self.policies if r.decisions]
        return self._spread(shares)[0] if shares else 0.0

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

        p95 = lambda r: t["p95"] if (t := self._ttft(r)) else None  # noqa: E731
        base_t = self._arm(self.baselines, p95)
        pol_t = self._arm(self.policies, p95)
        base_g = self._arm(self.baselines, self._goodput)
        pol_g = self._arm(self.policies, self._goodput)

        def band(s: tuple[float, float, float], fmt: str) -> str:
            """Median, and the observed range when there is more than one run. A bare
            number from one run reads as more certain than it is."""
            median, lo, hi = s
            if self.repeats < 2 or lo == hi:
                return fmt.format(median)
            return f"{fmt.format(median)} ({fmt.format(lo)}-{fmt.format(hi)})"

        if base_t and pol_t and base_t[0]:
            factor = base_t[0] / pol_t[0] if pol_t[0] else float("inf")
            direction = "lower" if factor > 1 else "HIGHER"
            out.append(
                f"  p95 TTFT of served requests is {abs(factor):.2f}x {direction}: "
                f"{band(base_t, '{:.0f}')}ms -> {band(pol_t, '{:.0f}')}ms"
            )
        if base_g and pol_g:
            verb = "up" if pol_g[0] > base_g[0] else "DOWN"
            out.append(
                f"  goodput is {verb}: {band(base_g, '{:.3f}')} -> "
                f"{band(pol_g, '{:.3f}')}  (of offered)"
            )
            if pol_g[0] < base_g[0]:
                out += [
                    "",
                    "  Goodput fell, so the policy refused requests the cluster could have",
                    "  served. Faster tails bought at that price are not a win.",
                ]

        sep = self.separated()
        if sep is False:
            out += [
                "",
                f"  BUT the two arms overlap across {self.repeats} repeats, so this gap is",
                "  inside the run-to-run noise. It is not a result yet — more repeats, or a",
                "  larger effect.",
            ]
        return out

    def _table(self) -> list[str]:
        def med(reports: list[Report], metric, fmt: str = "{:.0f}") -> str:
            s = self._arm(reports, metric)
            return "--" if s is None else fmt.format(s[0])

        p50 = lambda r: t["p50"] if (t := self._ttft(r)) else None  # noqa: E731
        p95 = lambda r: t["p95"] if (t := self._ttft(r)) else None  # noqa: E731

        rows = [
            ("policy", self.baseline.policy or "-", self.policy.policy or "-"),
            ("repeats", str(len(self.baselines)), str(len(self.policies))),
            (
                "offered",
                med(self.baselines, lambda r: float(len(r.decisions))),
                med(self.policies, lambda r: float(len(r.decisions))),
            ),
            (
                "refused",
                med(self.baselines, lambda r: float(r.refused)),
                med(self.policies, lambda r: float(r.refused)),
            ),
            ("p50 TTFT ms", med(self.baselines, p50), med(self.policies, p50)),
            ("p95 TTFT ms", med(self.baselines, p95), med(self.policies, p95)),
            (
                "goodput",
                med(self.baselines, self._goodput, "{:.3f}"),
                med(self.policies, self._goodput, "{:.3f}"),
            ),
        ]
        label_row = "median of repeats" if self.repeats > 1 else ""
        out = [f"  {label_row:<16} {'baseline':>14} {'with policy':>14}", ""]
        out += [f"  {label:<16} {a:>14} {b:>14}" for label, a, b in rows]

        # Signals, because "the policy changed the cluster" is checkable rather than
        # assumed: KV pressure that did not move means the refusals bought nothing.
        shared = [n for n in self.policy.signals() if n in self.baseline.signals()]
        if shared:
            out += ["", f"  {'signal max':<16} {'baseline':>14} {'with policy':>14}", ""]
            for name in shared:
                top = lambda r, n=name: (  # noqa: E731
                    s["max"] if (s := r.signal_range(n)) else None
                )
                out.append(
                    f"  {name:<16} {med(self.baselines, top, '{:.3g}'):>14} "
                    f"{med(self.policies, top, '{:.3g}'):>14}"
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
        sep = self.separated()
        if sep is None:
            out.append("--  only one run per arm, so there is no error bar")
        else:
            out.append(f"  {mark[sep]} the arms separate across {self.repeats} repeats")
        for name, reports in (("baseline", self.baselines), ("policy", self.policies)):
            for i, r in enumerate(reports, 1):
                for fault in r.fault_text():
                    tag = f"{name}" if len(reports) == 1 else f"{name} r{i}"
                    out.append(f"  !! {tag}:{fault.strip()}")

        ok = self.fired() and self.comparable_load() and self.measurable()
        if ok and sep:
            out += [
                "",
                "  All four hold, so the difference above is the policy and it is larger",
                "  than the noise between repeats. This is a result you can quote.",
            ]
        elif ok and sep is None:
            out += [
                "",
                "  The first three hold, but one run of each has no error bar. Run the pair",
                "  again — a gap smaller than the spread between repeats is not a result:",
                "",
                "      python examples/compare_two_policies.py --repeats 5",
            ]
        return out
