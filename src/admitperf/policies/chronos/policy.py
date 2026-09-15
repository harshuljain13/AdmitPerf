"""Deadline-aware admission, in the spirit of Chronos.

Different in kind from `queue_depth` and `kv_threshold`. Those read a busyness
signal and refuse past a threshold — they do not know what any individual
request needs. This one predicts *this* request's response time and refuses it
only if that prediction misses *its* deadline. Two requests arriving into the
same fleet state can get different answers, because they asked for different
things.

The name carries "inspired" for a reason, per CONTRIBUTING's fidelity rule.
Chronos derives sound worst-case bounds from per-task knowledge: arrival
periods, execution-time bounds, the state of every queued task. A Prometheus
endpoint reports counts, not per-request state, so the queue ahead is
characterised by its size and a mean. The shape of the reasoning is the
paper's; the guarantee is not.
"""

from __future__ import annotations

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState
from admitperf.policies.chronos.estimator import ServiceRateEstimator
from admitperf.policies.chronos.wcrt import predict


class ChronosInspiredWCRT(AdmissionPolicy):
    """Admit only if the predicted response time fits the request's deadline."""

    name = "chronos_inspired"
    requires = frozenset({"running_requests", "waiting_requests"})

    def __init__(
        self,
        *,
        concurrency: int = 8,
        safety_factor: float = 1.0,
        mean_output_tokens: float = 128.0,
        defer_instead_of_reject: bool = False,
        retry_after_ms: int = 250,
    ) -> None:
        if concurrency < 1:
            raise ValueError(f"concurrency must be >= 1, got {concurrency}")
        if safety_factor <= 0:
            raise ValueError(f"safety_factor must be > 0, got {safety_factor}")

        self.concurrency = concurrency
        self.safety_factor = safety_factor
        self.mean_output_tokens = mean_output_tokens
        self.defer_instead_of_reject = defer_instead_of_reject
        self.retry_after_ms = retry_after_ms
        self.estimator = ServiceRateEstimator()

        # Kept for inspection after a run: a policy that never rejected
        # because it was never calibrated looks identical to one that judged
        # every request admissible, and the difference matters.
        self.decisions_uncalibrated = 0
        self.decisions_predicted = 0

    def decide(self, req: Request, state: SystemState) -> Decision:
        rates = self.estimator.update(state)

        if not rates.is_calibrated:
            # Nothing has completed yet, so there is no basis for a prediction.
            # Admitting is the honest choice: refusing on no information would
            # shed the very traffic needed to calibrate, and the run would
            # never recover.
            self.decisions_uncalibrated += 1
            return Decision.admit()

        if req.deadline_ttft_ms is None and req.deadline_tbt_ms is None:
            # Nothing to judge against. A deadline-aware policy has no opinion
            # on a request that states no deadline.
            return Decision.admit()

        self.decisions_predicted += 1
        prediction = predict(
            input_tokens=req.input_tokens,
            expected_output_tokens=req.expected_output_tokens,
            running=state.running_requests,
            waiting=state.waiting_requests,
            concurrency=self.concurrency,
            rates=rates,
            deadline_ttft_ms=req.deadline_ttft_ms,
            deadline_tbt_ms=req.deadline_tbt_ms,
            mean_output_tokens=self.mean_output_tokens,
            safety_factor=self.safety_factor,
        )

        if prediction.fits:
            return Decision.admit()

        reason = "deadline_unmeetable" if prediction.violated == "ttft" else "tbt_unmeetable"
        if self.defer_instead_of_reject:
            # Worth contrasting: holding converts a refusal into latency, and
            # the queue may drain enough for the same request to fit later.
            return Decision.defer(retry_after_ms=self.retry_after_ms, reason=reason)
        return Decision.reject(reason=reason)


__all__ = ["ChronosInspiredWCRT"]
