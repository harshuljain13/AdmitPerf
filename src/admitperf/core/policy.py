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

    #: True for a policy that refuses nothing BY DESIGN. Recorded in the log so a
    #: report can tell a baseline from a policy that failed to fire — otherwise the
    #: comparison arm gets reported as a broken run and the reader is told to change
    #: the load, which is exactly backwards.
    baseline: ClassVar[bool] = False

    def __init__(self, *, log: str | None = None, enforce: float = 1.0, **params: Any) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} needs a `name`")
        if not 0.0 <= enforce <= 1.0:
            raise ValueError(f"enforce must be in [0, 1], got {enforce}")
        self.log = Log(log)
        #: Fraction of traffic actually subject to the verdict. Below 1.0 the rest is
        #: measured but not governed, so one run gives you both arms under identical
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
                "policy": self.name,
                "baseline": self.baseline,
                "params": self.params,
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
