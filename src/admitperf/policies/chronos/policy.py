"""Chronos Algorithm 1, ported to a live serving engine.

Different in kind from `queue_depth` and `kv_threshold`. Those read a busyness
signal and refuse past a threshold; they cannot tell an interactive request
from a batch one. This runs a per-request *feasibility test* — is there a
schedule in which this request meets its deadline? — so identical fleet state
yields different answers for different SLO classes.

Source: Marref, Tarmissi & Chaibi, *Frontiers in Computer Science* 8 (2026),
DOI 10.3389/fcomp.2026.1873627.

## Why the name says "inspired"

The published artifact is a discrete-event simulator with roofline-derived
kernel parameters, evaluated on replayed Azure traces. This runs Algorithm 1
against vLLM with parameters fitted from live telemetry. The algorithm is the
paper's; the substrate, the parameters and therefore the numbers are not. Two
further gaps are load-bearing:

- **Utilization is inferred, not known.** The paper computes rho_P from the
  arrival process it controls. Here lambda comes from a sliding window over
  observed arrivals and E[C_pre] from a fitted cost model, so rho_P is an
  estimate and the bound inherits its error.
- **gamma is not fitted.** Separating fixed per-iteration overhead from the
  per-token slope needs measurements at two or more batch sizes; the engine's
  aggregate counters cannot provide that. The paper's value is used.

Per CONTRIBUTING's fidelity rule that forbids the bare name. See
reports/chronos-reproduction.md.
"""

from __future__ import annotations

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState
from admitperf.policies.chronos.estimator import (
    ArrivalRateWindow,
    ServiceRateEstimator,
    fit_cost_model,
)
from admitperf.policies.chronos.wcrt import CostModel, admission_test

#: Algorithm 1's failure modes, mapped onto AdmitPerf reject reasons so a
#: results bundle records which of the three checks refused the request.
REASONS = {
    "overloaded": "overloaded",
    "ttft_infeasible": "deadline_unmeetable",
    "tbt_capacity": "tbt_unmeetable",
}


class ChronosInspiredWCRT(AdmissionPolicy):
    """Admit only if a formal feasibility test says the deadline can be met."""

    name = "chronos_inspired"
    requires = frozenset({"running_requests", "waiting_requests"})

    def __init__(
        self,
        *,
        chunk_tokens: int = 512,
        window_s: float = 60.0,
        safety_factor: float = 1.0,
        fit_from_telemetry: bool = True,
        defer_instead_of_reject: bool = False,
        retry_after_ms: int = 250,
        alpha_ms_per_token: float | None = None,
        beta_ms_per_token: float | None = None,
        gamma_ms: float | None = None,
    ) -> None:
        if safety_factor <= 0:
            raise ValueError(f"safety_factor must be > 0, got {safety_factor}")
        if chunk_tokens < 1:
            raise ValueError(f"chunk_tokens must be >= 1, got {chunk_tokens}")

        base = CostModel(chunk_tokens=chunk_tokens)
        self.base_cost = CostModel(
            alpha_ms_per_token=alpha_ms_per_token or base.alpha_ms_per_token,
            beta_ms_per_token=beta_ms_per_token or base.beta_ms_per_token,
            gamma_ms=base.gamma_ms if gamma_ms is None else gamma_ms,
            chunk_tokens=chunk_tokens,
        )
        self.safety_factor = safety_factor
        self.fit_from_telemetry = fit_from_telemetry
        self.defer_instead_of_reject = defer_instead_of_reject
        self.retry_after_ms = retry_after_ms

        self.rates = ServiceRateEstimator()
        self.arrivals = ArrivalRateWindow(window_s=window_s)

        #: Per-check tallies. A policy that admitted everything because it was
        #: never calibrated looks identical to one that found everything
        #: feasible, and the difference decides whether a run means anything.
        self.counts = {
            "admitted": 0,
            "overloaded": 0,
            "ttft_infeasible": 0,
            "tbt_capacity": 0,
            "uncalibrated": 0,
            "no_deadline": 0,
        }
        #: Last test, for inspection and debugging.
        self.last_test = None

    def decide(self, req: Request, state: SystemState) -> Decision:
        self.arrivals.record(req.arrival_time)
        rates = self.rates.update(state)

        if self.fit_from_telemetry and not rates.is_calibrated:
            # Nothing has completed, so alpha and beta cannot be fitted.
            # Admitting is the honest choice: refusing on no information would
            # shed the very traffic needed to calibrate and never recover.
            self.counts["uncalibrated"] += 1
            return Decision.admit()

        if req.deadline_ttft_ms is None and req.deadline_tbt_ms is None:
            # A feasibility test needs something to be feasible against.
            self.counts["no_deadline"] += 1
            return Decision.admit()

        cost = (
            fit_cost_model(rates, base=self.base_cost, chunk_tokens=self.base_cost.chunk_tokens)
            if self.fit_from_telemetry
            else self.base_cost
        )

        test = admission_test(
            input_tokens=req.input_tokens,
            deadline_ttft_ms=req.deadline_ttft_ms,
            deadline_tbt_ms=req.deadline_tbt_ms,
            arrival_rate_hz=self.arrivals.peak_rate_hz(req.arrival_time),
            mean_prefill_wcet_ms=cost.prefill_wcet_ms(req.input_tokens),
            active_decode_tasks=state.running_requests,
            cost=cost,
            safety_factor=self.safety_factor,
        )
        self.last_test = test  # type: ignore[assignment]

        if test.admit:
            self.counts["admitted"] += 1
            return Decision.admit()

        assert test.failed is not None
        self.counts[test.failed] += 1
        reason = REASONS[test.failed]

        if self.defer_instead_of_reject:
            # Worth contrasting with rejection: holding converts a refusal into
            # latency, and utilization may fall enough for the same request to
            # pass the test later.
            return Decision.defer(retry_after_ms=self.retry_after_ms, reason=reason)
        return Decision.reject(reason=reason)


__all__ = ["REASONS", "ChronosInspiredWCRT"]
