"""SLO accounting.

The failure this guards against: a policy that refuses most traffic and serves
the remainder well looks excellent on the wrong denominator.
"""

from __future__ import annotations

import pytest

from admitperf.bench.slo import Attainment, judge, percentile
from admitperf.core.api import Request
from admitperf.core.ports import RequestOutcome


def _req(*, ttft: float | None = 500.0, itl: float | None = 50.0) -> Request:
    return Request(
        request_id="r1",
        tenant_id="t",
        arrival_time=0.0,
        input_tokens=100,
        deadline_ttft_ms=ttft,
        deadline_tbt_ms=itl,
    )


def _out(
    *, status: str = "completed", ttft: float | None = 100.0, gaps=(10.0, 12.0), tokens: int = 50
):
    return RequestOutcome(
        request_id="r1", status=status, ttft_ms=ttft, tbt_ms=tuple(gaps), output_tokens=tokens
    )


# --- judging one request --------------------------------------------------


def test_both_promises_must_hold() -> None:
    """Starting promptly then stalling mid-answer is not being served well."""
    assert judge(_req(), _out(ttft=100.0, gaps=(10.0, 12.0))).met
    assert not judge(_req(), _out(ttft=9999.0)).met  # slow first token
    assert not judge(_req(), _out(gaps=(10.0, 900.0))).met  # stalled stream


def test_inter_token_uses_p95_not_the_mean() -> None:
    """A mean hides the stalls preemption causes, which is the thing an
    admission policy is supposed to prevent."""
    # 94 smooth gaps and 6 stalls: mean 33ms passes a 50ms budget, p95 400ms
    # does not. The mean would call this request well served.
    gaps = tuple([10.0] * 94 + [400.0] * 6)
    assert sum(gaps) / len(gaps) < 50.0
    assert percentile(gaps, 0.95) == 400.0
    assert not judge(_req(itl=50.0), _out(gaps=gaps)).met


def test_the_failed_promise_is_named() -> None:
    assert judge(_req(), _out(ttft=9999.0)).failed == "ttft"
    assert judge(_req(), _out(gaps=(10.0, 900.0))).failed == "itl"


def test_a_failed_request_counts_as_judged_and_missed() -> None:
    """Treating it as unjudged would remove it from the denominator, rewarding
    a policy for admitting requests that then error."""
    verdict = judge(_req(), _out(status="failed", ttft=None, gaps=()))
    assert verdict.judged and not verdict.met


def test_a_request_promised_nothing_is_not_judged() -> None:
    """Excluded from attainment rather than counted as a free win."""
    assert not judge(_req(ttft=None, itl=None), _out()).judged


def test_only_one_promise_is_enough_to_judge() -> None:
    assert judge(_req(ttft=500.0, itl=None), _out(ttft=100.0)).met


# --- the two denominators -------------------------------------------------


def test_served_flatters_shedding_and_offered_does_not() -> None:
    """The central point. A policy admitting 5 of 100 and serving them
    perfectly scores 1.00 served and 0.05 offered."""
    att = Attainment(arrived=100, admitted=5, rejected=95)
    for _ in range(5):
        att.add(judge(_req(), _out()), _out())

    assert att.served == pytest.approx(1.0)
    assert att.offered == pytest.approx(0.05)


def test_offered_cannot_be_improved_by_refusing_more() -> None:
    """Refusing better raises it; refusing more does not."""
    lenient = Attainment(arrived=100, admitted=100, rejected=0)
    for i in range(100):
        lenient.add(judge(_req(), _out(ttft=100.0 if i < 40 else 9999.0)), _out())

    strict = Attainment(arrived=100, admitted=40, rejected=60)
    for _ in range(40):
        strict.add(judge(_req(), _out(ttft=100.0)), _out())

    assert lenient.offered == pytest.approx(0.40)
    assert strict.offered == pytest.approx(0.40)  # same offered...
    assert strict.served > lenient.served  # ...very different served


def test_wasted_tokens_measure_what_a_policy_saved() -> None:
    att = Attainment(arrived=2, admitted=2)
    att.add(judge(_req(), _out(ttft=100.0, tokens=50)), _out(tokens=50))
    att.add(judge(_req(), _out(ttft=9999.0, tokens=150)), _out(ttft=9999.0, tokens=150))

    assert att.useful_output_tokens == 50
    assert att.wasted_output_tokens == 150
    assert att.wasted_fraction == pytest.approx(0.75)


def test_empty_accounting_reports_none_not_zero() -> None:
    """Zero would read as a run that served nothing, rather than one that
    measured nothing."""
    att = Attainment()
    assert att.offered is None and att.served is None and att.wasted_fraction is None


# --- percentile -----------------------------------------------------------


def test_percentile_is_nearest_rank() -> None:
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 95.0


def test_percentile_of_nothing_is_none() -> None:
    assert percentile([], 0.95) is None
