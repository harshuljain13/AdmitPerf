"""Chronos response-time analysis.

Pure functions, so these tests need no engine, mock or clock — the reason the
maths lives in its own module. Values are checked against the theorems as
stated in the paper (DOI 10.3389/fcomp.2026.1873627), not against our own
implementation's output.
"""

from __future__ import annotations

import pytest

from admitperf.policies.chronos.wcrt import (
    CostModel,
    admission_test,
    prefill_utilization,
    theorem1_wcrt_ms,
    theorem3_max_decode_tasks,
)

#: The paper's roofline parameters: 7B dense model on an A100-80GB, fp16.
PAPER = CostModel(alpha_ms_per_token=0.080, beta_ms_per_token=0.250, gamma_ms=7.0, chunk_tokens=512)


# --- cost model -----------------------------------------------------------


def test_prefill_chunks_round_up() -> None:
    """p_i = ceil(l_i / B)."""
    assert PAPER.prefill_chunks(512) == 1
    assert PAPER.prefill_chunks(513) == 2
    assert PAPER.prefill_chunks(1) == 1


def test_prefill_wcet_follows_the_paper_formula() -> None:
    """C_pre = p_i * (alpha*B + gamma); one chunk = 0.080*512 + 7 = 47.96ms."""
    assert PAPER.prefill_wcet_ms(512) == pytest.approx(47.96)
    assert PAPER.prefill_wcet_ms(1024) == pytest.approx(95.92)


# --- utilization ----------------------------------------------------------


def test_utilization_is_rate_times_service_time() -> None:
    """rho_P = lambda * E[C_pre]. 10/s at 50ms each = 50% of capacity."""
    assert prefill_utilization(arrival_rate_hz=10.0, mean_prefill_wcet_ms=50.0) == pytest.approx(
        0.5
    )


def test_negative_inputs_are_refused() -> None:
    with pytest.raises(ValueError):
        prefill_utilization(arrival_rate_hz=-1.0, mean_prefill_wcet_ms=10.0)


# --- Theorem 1 ------------------------------------------------------------


def test_theorem1_matches_the_closed_form() -> None:
    """WCRT = rho*D/(1-rho) + C_pre. At rho=0.5, D=2000: 2000 + C_pre."""
    wcrt = theorem1_wcrt_ms(utilization=0.5, deadline_ttft_ms=2000.0, own_wcet_ms=100.0)
    assert wcrt == pytest.approx(2100.0)


def test_theorem1_diverges_as_utilization_approaches_one() -> None:
    """The formal statement of what saturation does."""
    a = theorem1_wcrt_ms(utilization=0.90, deadline_ttft_ms=2000.0, own_wcet_ms=100.0)
    b = theorem1_wcrt_ms(utilization=0.99, deadline_ttft_ms=2000.0, own_wcet_ms=100.0)
    assert a is not None and b is not None
    assert b > a * 5


def test_theorem1_does_not_hold_at_or_above_full_utilization() -> None:
    """None, not infinity: an unschedulable system is caught by the
    utilization check before any per-request bound is meaningful."""
    assert theorem1_wcrt_ms(utilization=1.0, deadline_ttft_ms=2000.0, own_wcet_ms=100.0) is None


def test_the_bound_implies_a_fifty_percent_utilization_ceiling() -> None:
    """A consequence worth pinning: rho*D/(1-rho) <= D requires rho <= 0.5, so
    the test refuses everything above roughly half utilization however generous
    the deadline. This is why the paper reports admission rates near 50% at
    10x nominal load."""
    below = theorem1_wcrt_ms(utilization=0.49, deadline_ttft_ms=2000.0, own_wcet_ms=0.0)
    above = theorem1_wcrt_ms(utilization=0.51, deadline_ttft_ms=2000.0, own_wcet_ms=0.0)
    assert below is not None and above is not None
    assert below < 2000.0 < above


# --- Theorem 3 ------------------------------------------------------------


def test_theorem3_bounds_concurrent_decode_tasks() -> None:
    """n_D <= (s - gamma) / beta. At s=200ms this reading gives 772; the paper
    reports ~731, a discrepancy recorded rather than tuned away."""
    assert theorem3_max_decode_tasks(tbt_slo_ms=200.0, cost=PAPER) == 772


def test_a_budget_below_fixed_overhead_admits_nothing() -> None:
    """gamma is paid per iteration regardless of batch size, so a TBT SLO
    tighter than gamma is unachievable at any concurrency."""
    assert theorem3_max_decode_tasks(tbt_slo_ms=5.0, cost=PAPER) == 0


def test_tighter_budgets_allow_fewer_tasks() -> None:
    assert theorem3_max_decode_tasks(tbt_slo_ms=50.0, cost=PAPER) < theorem3_max_decode_tasks(
        tbt_slo_ms=200.0, cost=PAPER
    )


# --- Algorithm 1 ----------------------------------------------------------


def _test(**kw):
    base = dict(
        input_tokens=512,
        deadline_ttft_ms=2000.0,
        deadline_tbt_ms=200.0,
        arrival_rate_hz=1.0,
        mean_prefill_wcet_ms=47.96,
        active_decode_tasks=0,
        cost=PAPER,
    )
    return admission_test(**{**base, **kw})


def test_a_lightly_loaded_system_admits() -> None:
    assert _test().admit


def test_check_a_rejects_an_overloaded_system() -> None:
    """rho >= 1: no per-request bound holds, so nothing is admissible."""
    result = _test(arrival_rate_hz=100.0, mean_prefill_wcet_ms=50.0)
    assert not result.admit
    assert result.failed == "overloaded"
    assert result.wcrt_ms is None


def test_check_b_rejects_an_infeasible_deadline() -> None:
    result = _test(arrival_rate_hz=15.0, deadline_ttft_ms=100.0)
    assert not result.admit
    assert result.failed == "ttft_infeasible"


def test_check_c_rejects_when_decode_capacity_is_exhausted() -> None:
    result = _test(active_decode_tasks=10_000)
    assert not result.admit
    assert result.failed == "tbt_capacity"


def test_the_checks_run_in_the_papers_order() -> None:
    """Overload is reported ahead of an infeasible deadline: the utilization
    check is about the system, the WCRT check about one request."""
    result = _test(arrival_rate_hz=100.0, mean_prefill_wcet_ms=50.0, deadline_ttft_ms=1.0)
    assert result.failed == "overloaded"


def test_same_load_different_deadline_different_verdict() -> None:
    """The behaviour no threshold policy reproduces: identical system state,
    opposite answers, because the requests asked for different things."""
    load = dict(arrival_rate_hz=8.0, mean_prefill_wcet_ms=47.96)
    assert not _test(**load, deadline_ttft_ms=100.0).admit
    assert _test(**load, deadline_ttft_ms=60_000.0).admit


def test_the_deadline_appears_on_both_sides_of_the_bound() -> None:
    """A property of Theorem 1 that is easy to miss and shapes its behaviour.

    D_TTFT is inside the bound as well as being the thing compared against, so
    halving the deadline also roughly halves the predicted WCRT. Feasibility is
    therefore governed by utilization and the request's own prefill cost, not
    by the deadline in the way a naive reading suggests — and it is why the
    test discriminates between SLO classes only once C_pre is comparable to D.
    """
    tight = theorem1_wcrt_ms(utilization=0.4, deadline_ttft_ms=1000.0, own_wcet_ms=0.0)
    loose = theorem1_wcrt_ms(utilization=0.4, deadline_ttft_ms=2000.0, own_wcet_ms=0.0)
    assert tight is not None and loose is not None
    assert loose == pytest.approx(tight * 2)

    # With no own-WCET term the verdict is identical at both deadlines.
    ratio_only = 0.4 / (1 - 0.4)
    assert ratio_only < 1.0  # feasible at every deadline, since WCRT = ratio * D


def test_safety_factor_sheds_earlier() -> None:
    """Not in the paper — the bound is sound there. It exists because our
    parameters are fitted from noisy telemetry rather than derived."""
    load = dict(arrival_rate_hz=9.0, deadline_ttft_ms=1200.0)
    assert _test(**load, safety_factor=1.0).admit
    assert not _test(**load, safety_factor=3.0).admit


def test_the_test_reports_its_own_inputs() -> None:
    """Utilization and the bound reach the results bundle, so a rejection can
    be explained after the fact rather than merely counted."""
    result = _test(arrival_rate_hz=5.0)
    assert 0.0 < result.utilization < 1.0
    assert result.wcrt_ms is not None
    assert result.own_wcet_ms == pytest.approx(47.96)


# --- rho_P is a statement about offered load, not about one request ---------


def test_expected_prefill_cost_is_a_window_mean() -> None:
    """E[p] over arrivals, so a long prompt cannot make the fleet look busy on
    its own account."""
    from admitperf.policies.chronos.estimator import PrefillDemandWindow

    window = PrefillDemandWindow(window_s=60.0)
    assert window.mean_chunks(now=0.0) is None, "nothing has arrived yet"

    for chunks, t in ((1, 0.0), (1, 0.1), (1, 0.2), (8, 0.3)):
        window.record(chunks, now=t)
    assert window.mean_chunks(now=0.3) == pytest.approx((1 + 1 + 1 + 8) / 4)


def test_old_arrivals_leave_the_window() -> None:
    from admitperf.policies.chronos.estimator import PrefillDemandWindow

    window = PrefillDemandWindow(window_s=10.0)
    window.record(8, now=0.0)
    window.record(1, now=100.0)
    assert window.mean_chunks(now=100.0) == pytest.approx(1.0)


def test_a_long_prompt_alone_does_not_trip_the_overload_check() -> None:
    """The defect this corrected: at 512-token chunks a 4096-token request
    scored eight times a 512-token one, so a mixed workload refused its longest
    class however idle the fleet was."""
    from admitperf.core.api import Request, SystemState
    from admitperf.policies.chronos.policy import ChronosInspiredWCRT

    policy = ChronosInspiredWCRT(fit_from_telemetry=False, chunk_tokens=512)
    state = SystemState(
        now=0.0,
        kv_used_fraction=0.0,
        running_requests=0,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )

    # A stream of short prompts, then one long one at the same modest rate.
    for i in range(20):
        policy.decide(
            Request(
                request_id=f"short-{i}",
                tenant_id="t",
                arrival_time=i * 0.25,
                input_tokens=256,
                expected_output_tokens=64,
                deadline_ttft_ms=5_000.0,
            ),
            state,
        )
    decision = policy.decide(
        Request(
            request_id="long",
            tenant_id="t",
            arrival_time=5.25,
            input_tokens=4096,
            expected_output_tokens=64,
            deadline_ttft_ms=5_000.0,
        ),
        state,
    )
    assert policy.last_test is not None
    assert policy.last_test.utilization < 1.0, (
        f"one long prompt among short ones reported rho_P={policy.last_test.utilization:.2f}"
    )
    assert decision.kind != "reject" or policy.last_test.failed != "overloaded"
