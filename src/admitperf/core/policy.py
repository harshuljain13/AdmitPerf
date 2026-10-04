"""The base class you subclass."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from admitperf.core.decision import Decision
from admitperf.core.log import Log
from admitperf.core.signals import ALL
from admitperf.core.verdict import Verdict

_ADMIT = Decision(Verdict.ADMIT)


class Policy:
    """Decide on signals; every call is recorded.

        from admitperf import Policy
        from admitperf.core.signals import KV_PRESSURE

        class KvWall(Policy):
            name = "kv_wall"

            def decide(self, metrics):
                if KV_PRESSURE.read(metrics) >= self.threshold:
                    return self.reject("kv_pressure")
                return self.admit()

    In your gateway:

        policy = KvWall(threshold=0.90, log="decisions.jsonl")

        d = policy(raw_metrics, request_id=rid)      # whatever you scraped
        if not d.admitted:
            return Response(d.status, retry_after=d.retry_after_ms)

    Recording is not opt-in; subclassing is. There is no instrumented and
    uninstrumented version to pick between, and no second object to construct.

    AdmitPerf fetches nothing, provisions nothing, and does not enforce its own
    verdict — your gateway returns the response.
    """

    name: ClassVar[str] = ""

    #: What to do if `decide` raises. Admit, because failing closed on a bug sheds
    #: all traffic, which is worse than the bug.
    on_error: ClassVar[Verdict] = Verdict.ADMIT

    # --- the survey's taxonomy axes -------------------------------------
    # What places this policy in the applicability table, so a result here can sit
    # beside a published one. Declared on the class because they are properties of the
    # policy, not of a run.

    #: Axis 1 — what is admitted: request | agent-session | tenant.
    unit: ClassVar[str] = "request"

    #: Axis 2 — online | offline | hybrid.
    setting: ClassVar[str] = "online"

    #: Axis 3 — what it protects: deadline | throughput | fairness | cost | stability.
    slo_awareness: ClassVar[str] = "throughput"

    #: Axis 4 — the taxonomy's term for what it watches, which is NOT the signal's
    #: name here: kv_pressure | queue_depth | deadline_slack | wait_estimate |
    #: predicted_length | batch_state | rate | analytic. The quantity is what makes two
    #: policies comparable across papers; a signal name is this implementation's
    #: plumbing.
    signal_quantity: ClassVar[str | None] = None

    #: Axis 4 sub-branch, on STRUCTURE rather than content: scalar | dual-gate |
    #: lp-composite | formal-bound. This decides what liveness can mean — a dual gate
    #: can hold its first signal above threshold all run and never fire, so one
    #: signal's range does not establish liveness for it.
    signal_structure: ClassVar[str] = "scalar"

    #: True for a policy that refuses nothing BY DESIGN. Recorded in the log so a
    #: report can tell a baseline from a policy that failed to fire — otherwise the
    #: baseline gets reported as a broken run and the reader is told to change
    #: the load, which is exactly backwards.
    baseline: ClassVar[bool] = False

    def __init__(
        self,
        *,
        log: str | None = None,
        experiment: str | None = None,
        run: str | None = None,
        notes: str | None = None,
        provenance: dict[str, str] | None = None,
        enforce: float = 1.0,
        **params: Any,
    ) -> None:
        """
        experiment
            What question this measurement answers — "kv-wall-8k", "overload-2.5x".
            Policies sharing an experiment are comparable; policies that do not are
            not, and a report should refuse to pretend otherwise. Declared, because a
            file path is not an identity: inferring it from a directory layout means
            moving a file changes what the result claims to be.
        run
            Which repeat this is — "r1", "r2". One run of a policy has no error bar, so
            the report needs to know which runs belong together.
        notes
            Free text for what a reader needs and the log cannot know: the hardware,
            the workload, the engine version. Reporting item 7.
        provenance
            Per parameter, where its value came from — "fitted", "inherited", or
            "default". Reporting item 5: a policy carrying a hardware-dependent
            constant cannot be reproduced without it, and a threshold inherited from a
            paper run on other hardware is a different claim from one fitted here.
        """
        if not self.name:
            raise ValueError(f"{type(self).__name__} needs a `name`")
        if not 0.0 <= enforce <= 1.0:
            raise ValueError(f"enforce must be in [0, 1], got {enforce}")
        self.log = Log(log)
        self.experiment = experiment
        self.run = run
        self.notes = notes
        self.provenance = dict(provenance or {})
        #: Fraction of traffic actually subject to the verdict. Below 1.0 the rest is
        #: measured but not governed, so one run gives you both sides under identical
        #: conditions — and caps the blast radius in production.
        self.enforce = enforce
        self.params = dict(params)
        for key, value in params.items():
            setattr(self, key, value)  # self.threshold, not self.p.threshold

    # --- what you write ---------------------------------------------------

    def decide(self, metrics: Mapping[str, float]) -> Decision:
        raise NotImplementedError(f"{type(self).__name__} must implement decide()")

    def admit(self) -> Decision:
        return _ADMIT

    def reject(self, reason: str) -> Decision:
        return Decision(Verdict.REJECT, reason)

    def defer(self, reason: str, retry_after_ms: int) -> Decision:
        return Decision(Verdict.DEFER, reason, retry_after_ms)

    # --- what your gateway calls ------------------------------------------

    def __call__(self, metrics: Mapping[str, float], *, request_id: str | None = None) -> Decision:
        """Decide, and record.

        Every shipped signal is read for the record, not only the ones this policy
        used. That asymmetry is what lets a later report say "your signal never moved
        but queue depth hit 61", and what makes replaying a different policy over the
        same log possible at all.
        """
        started = time.perf_counter()
        fault: str | None = None
        try:
            decision = self.decide(metrics)
        except Exception as exc:  # noqa: BLE001 - a policy bug must not 500 the host
            fault = f"{type(exc).__name__}: {exc}"
            decision = (
                _ADMIT
                if self.on_error is Verdict.ADMIT
                else Decision(self.on_error, "policy_error")
            )

        enforced = self._enforced(request_id)
        self.log.write(
            {
                "at": time.time(),
                # Identity first: experiment · policy · run is what groups a set of
                # logs into something comparable.
                "experiment": self.experiment,
                "run": self.run,
                "notes": self.notes,
                "policy": self.name,
                "baseline": self.baseline,
                "params": self.params,
                "provenance": self.provenance,
                # The survey's axes, so a report can place this policy in the
                # applicability table rather than a reader doing it by hand.
                "card": {
                    "unit": self.unit,
                    "setting": self.setting,
                    "slo_awareness": self.slo_awareness,
                    "signal_quantity": self.signal_quantity,
                    "signal_structure": self.signal_structure,
                },
                "request_id": request_id,
                "metrics": dict(metrics),
                "signals": {s.name: s.read(metrics) for s in ALL},
                "sources": {s.name: src for s in ALL if (src := s.source_of(metrics)) is not None},
                "verdict": decision.verdict.value,
                "reason": decision.reason,
                "status": decision.status,
                "retry_after_ms": decision.retry_after_ms,
                "decide_us": int((time.perf_counter() - started) * 1e6),
                "enforced": enforced,
                "fault": fault,
            }
        )
        return decision if enforced else _ADMIT

    def outcome(self, request_id: str, **facts: Any) -> None:
        """What happened to a request you admitted — `ttft_ms`, `ok`, `status`.

        Optional, and without it a report can say what the policy DID but never what
        it BOUGHT: no latency, no goodput. Your gateway already measures these.
        """
        self.log.write({"at": time.time(), "request_id": request_id, "outcome": facts})

    def close(self) -> None:
        """Flush. Without it the last buffered records are lost."""
        self.log.close()

    def __enter__(self) -> Policy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _enforced(self, request_id: str | None) -> bool:
        """Deterministic in the request id, not random, so a replay reproduces the
        split and a retried request is treated the same way twice — otherwise a
        client could succeed simply by retrying past a refusal."""
        if self.enforce >= 1.0 or request_id is None:
            return True
        if self.enforce <= 0.0:
            return False
        return (hash(request_id) % 1000) < self.enforce * 1000
