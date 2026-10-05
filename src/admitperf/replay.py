"""Run a policy over a trace that was recorded without it."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from admitperf.core.log import Log
from admitperf.core.policy import Policy


class Replay:
    """What a policy WOULD have done against a recorded cluster.

    This is what makes `admitperf watch` worth running. A watch trace holds metric
    snapshots and no decisions, so a report of it says UNKNOWN and leaves signal
    liveness unevidenced — the answer is sitting in the file with nothing to compare it
    against. Replaying a policy supplies the threshold, and the verdict follows.

    It needs no gateway, no integration, and no second run against the cluster. A policy
    is a pure function of metrics, so it cannot tell a recorded scrape from a live one.
    That also means N policies can be judged on ONE trace with identical inputs, which
    removes run-to-run variance from the comparison entirely.

    What it cannot tell you, and says so rather than implying otherwise: a metric
    snapshot is not a request. Samples are periodic, arrivals are not, so the fraction
    of samples at which a policy would have refused is a DUTY CYCLE — the share of the
    window spent in a refusing state — and not a refusal rate. The two differ by however
    bursty the traffic was. Turning one into the other needs arrival timestamps, which a
    watch trace does not carry.
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        #: Only samples that actually carried metrics. A failed scrape is recorded as a
        #: failure, and replaying a policy against an absent reading would invent a
        #: decision nobody could have made.
        self.samples = [r for r in records if r.get("metrics")]
        self.skipped = len(records) - len(self.samples)

    @classmethod
    def from_log(cls, path: str | Path) -> Replay:
        return cls(Log.read(path))

    def against(
        self,
        policy: Policy,
        *,
        out: str | Path,
        experiment: str,
        run: str = "replay",
        notes: str | None = None,
    ) -> Path:
        """Replay `policy` and write a decision log `report` and `compare` can read.

        The log is written in the same shape a live gateway writes, because every
        consumer downstream should be unable to tell the difference — the only thing
        that differs is where the metrics came from.
        """
        if not self.samples:
            # An empty log is indistinguishable from a policy that was never consulted,
            # and a report of it would say UNKNOWN as though nothing had been tried. The
            # CLI guards this too; the library has to, because a caller who skips the
            # CLI gets the same silent emptiness otherwise.
            raise ValueError(
                "nothing to replay against: no sample in this trace carried metrics. "
                "If every scrape failed, the trace records that, and a policy cannot "
                "have decided on a reading that was never taken."
            )

        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()

        replayed = type(policy)(
            log=str(out),
            experiment=experiment,
            run=run,
            notes=notes or f"replayed over {len(self.samples)} recorded samples",
            **policy.params,
        )
        with replayed as p:
            for i, sample in enumerate(self.samples):
                p(sample["metrics"], request_id=f"s{i}")
        return out

    def duty_cycle(self, policy: Policy) -> float:
        """Share of samples at which this policy would have refused.

        Deliberately not called a refusal rate. See the class docstring: samples are
        periodic and arrivals are not.
        """
        if not self.samples:
            return 0.0
        refused = sum(1 for s in self.samples if not _decide(policy, s["metrics"]).admitted)
        return refused / len(self.samples)


def _decide(policy: Policy, metrics: Mapping[str, float]):
    """One decision, without recording it.

    `Policy.__call__` writes a log entry; counting duty cycle should not. Calling
    `decide` directly skips the recording and the enforcement sampling, which is right
    here because a counterfactual is not a governed request.
    """
    try:
        return policy.decide(metrics)
    except Exception:  # noqa: BLE001 - a policy bug must not sink the whole replay
        from admitperf.core.decision import Decision
        from admitperf.core.verdict import Verdict

        return Decision(Verdict.ADMIT)
