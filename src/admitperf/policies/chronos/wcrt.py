"""Chronos response-time analysis: Theorem 1, Theorem 3, and the cost model.

Pure functions — inputs in, numbers out, no engine and no state. The arithmetic
is the part worth arguing about, so it is checkable without a GPU.

Source: Marref, Tarmissi & Chaibi, "Formal schedulability analysis for LLM
inference: TTFT and TBT deadline guarantees via response-time theory",
*Frontiers in Computer Science* 8 (2026). DOI 10.3389/fcomp.2026.1873627.
Reference implementation: github.com/am-research/rtss-ttft-tbt (simulator).

## The model

Classical real-time theory applied to the prefill/decode split. A request is a
**sporadic job**: prefill is released at arrival with an absolute TTFT
deadline, and on completion spawns a **decode task** whose every iteration must
land inside the TBT budget.

Prefill worst-case execution time is linear in chunks:

    p_i    = ceil(input_tokens / B)          chunks
    C_pre  = p_i * (alpha * B + gamma)       ms

**Theorem 1** bounds worst-case response time through a busy-period argument —
longest continuously-non-empty prefill period, interfering work from arrivals
within it, then a work-balance inequality:

    WCRT <= rho_P * D_TTFT / (1 - rho_P) + C_pre        valid iff rho_P < 1

where `rho_P` is prefill utilization. The bound diverges as utilization
approaches 1, which is the formal statement of what saturation does.

**Theorem 3** bounds concurrent decode tasks from the per-iteration budget:

    beta * n_D + gamma <= s   =>   n_D <= (s - gamma) / beta

## Measured rather than roofline

The paper derives alpha, beta and gamma from a roofline model of an A100 and
evaluates in its own discrete-event simulator, never on a serving engine. Its
limitations section flags that real measurements *could* replace them but the
artifact does not do so. Here they are fitted from live engine telemetry, which
answers that open question — and is also why results here are not directly
comparable to the paper's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    """The linear kernel model: alpha, beta, gamma.

    Defaults are the paper's roofline values for a 7B model on an A100-80GB,
    kept as a documented fallback for when telemetry has not yet produced a fit.
    """

    #: Prefill cost per token (ms). Paper: 0.080 for 7B on A100.
    alpha_ms_per_token: float = 0.080
    #: Decode cost per token per iteration (ms). Paper: 0.250.
    beta_ms_per_token: float = 0.250
    #: Fixed per-iteration overhead (ms). Paper: 7.0.
    gamma_ms: float = 7.0
    #: Prefill chunk size B, in tokens. vLLM's chunked-prefill budget.
    chunk_tokens: int = 512

    def prefill_chunks(self, input_tokens: int) -> int:
        """p_i = ceil(l_i / B)."""
        return max(1, math.ceil(input_tokens / self.chunk_tokens))

    def prefill_wcet_ms(self, input_tokens: int) -> float:
        """C_pre,i = p_i * (alpha * B + gamma)."""
        per_chunk = self.alpha_ms_per_token * self.chunk_tokens + self.gamma_ms
        return self.prefill_chunks(input_tokens) * per_chunk


def prefill_utilization(*, arrival_rate_hz: float, mean_prefill_wcet_ms: float) -> float:
    """rho_P = lambda * E[C_pre].

    The fraction of prefill capacity the offered load demands. At or above 1
    the system is formally overloaded and Theorem 1 does not hold — which is
    the first check of the admission test, not an edge case.
    """
    if arrival_rate_hz < 0 or mean_prefill_wcet_ms < 0:
        raise ValueError("arrival rate and WCET must be non-negative")
    return arrival_rate_hz * (mean_prefill_wcet_ms / 1000.0)


def theorem1_wcrt_ms(
    *, utilization: float, deadline_ttft_ms: float, own_wcet_ms: float
) -> float | None:
    """Worst-case response time bound. None when rho_P >= 1.

    None rather than infinity because the two differ in kind: an unschedulable
    system is caught by the utilization test before any per-request bound means
    anything.
    """
    if utilization >= 1.0:
        return None
    return utilization * deadline_ttft_ms / (1.0 - utilization) + own_wcet_ms


def theorem3_max_decode_tasks(*, tbt_slo_ms: float, cost: CostModel) -> int:
    """n*_D — concurrent decode tasks that still fit the per-iteration budget.

    From beta * n_D + gamma <= s. At the paper's parameters with s = 200ms this
    yields 772; the paper reports approximately 731, so the published figure
    carries a term this reading does not reproduce. Recorded rather than tuned
    away — see reports/chronos-reproduction.md.
    """
    headroom = tbt_slo_ms - cost.gamma_ms
    if headroom <= 0 or cost.beta_ms_per_token <= 0:
        return 0
    return int(headroom / cost.beta_ms_per_token)


@dataclass(frozen=True)
class AdmissionTest:
    """The outcome of Algorithm 1, with enough detail to explain itself."""

    admit: bool
    #: "overloaded" | "ttft_infeasible" | "tbt_capacity" | None
    failed: str | None
    utilization: float
    wcrt_ms: float | None
    own_wcet_ms: float
    max_decode_tasks: int
    active_decode_tasks: int


def admission_test(
    *,
    input_tokens: int,
    deadline_ttft_ms: float | None,
    deadline_tbt_ms: float | None,
    arrival_rate_hz: float,
    mean_prefill_wcet_ms: float,
    active_decode_tasks: int,
    cost: CostModel,
    safety_factor: float = 1.0,
) -> AdmissionTest:
    """Algorithm 1 — three checks, in the paper's order.

    (a) Utilization: reject if rho_P >= 1. The system is formally overloaded
        and no per-request bound holds.
    (b) TTFT feasibility: reject if the Theorem 1 bound exceeds this request's
        deadline.
    (c) TBT capacity: reject if one more decode task would push per-iteration
        time past the TBT budget (Theorem 3).

    `safety_factor` is not in the paper — there the bound is sound by
    construction. It exists here because alpha, beta and gamma are fitted from
    noisy telemetry rather than derived from a roofline.
    """
    own_wcet = cost.prefill_wcet_ms(input_tokens)
    rho = prefill_utilization(
        arrival_rate_hz=arrival_rate_hz, mean_prefill_wcet_ms=mean_prefill_wcet_ms
    )
    max_decode = (
        theorem3_max_decode_tasks(tbt_slo_ms=deadline_tbt_ms, cost=cost)
        if deadline_tbt_ms is not None
        else 0
    )

    def result(admit: bool, failed: str | None, wcrt: float | None) -> AdmissionTest:
        return AdmissionTest(
            admit=admit,
            failed=failed,
            utilization=rho,
            wcrt_ms=wcrt,
            own_wcet_ms=own_wcet,
            max_decode_tasks=max_decode,
            active_decode_tasks=active_decode_tasks,
        )

    if rho >= 1.0:
        return result(False, "overloaded", None)

    wcrt = None
    if deadline_ttft_ms is not None:
        wcrt = theorem1_wcrt_ms(
            utilization=rho, deadline_ttft_ms=deadline_ttft_ms, own_wcet_ms=own_wcet
        )
        if wcrt is None or wcrt * safety_factor > deadline_ttft_ms:
            return result(False, "ttft_infeasible", wcrt)

    if deadline_tbt_ms is not None and active_decode_tasks + 1 > max_decode:
        return result(False, "tbt_capacity", wcrt)

    return result(True, None, wcrt)


__all__ = [
    "AdmissionTest",
    "CostModel",
    "admission_test",
    "prefill_utilization",
    "theorem1_wcrt_ms",
    "theorem3_max_decode_tasks",
]
