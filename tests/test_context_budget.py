"""Traffic that cannot fit the engine's context window.

vLLM answers an over-long request with a 400, and the harness records it as an
admitted request that failed — which in the summary is indistinguishable from
the engine dropping it under load. On the first real A10G run this turned 32%
of the traffic into "failures" and dragged offered attainment to 0.025, for
both policies equally. It was a config mismatch, not a result.
"""

from __future__ import annotations

import pytest

from admitperf.bench.experiment import ContextOverflowError, check_context_budget
from admitperf.bench.workloads.poisson import SLOClass
from admitperf.core.config import ExperimentConfig

SMALL = SLOClass(
    name="small",
    weight=1.0,
    input_tokens=(64, 512),
    output_tokens=(32, 256),
    deadline_ttft_ms=500.0,
    deadline_tbt_ms=50.0,
    priority=10,
)
HUGE = SLOClass(
    name="huge",
    weight=1.0,
    input_tokens=(512, 4096),
    output_tokens=(512, 2048),
    deadline_ttft_ms=30000.0,
    deadline_tbt_ms=None,
    priority=0,
)


def _cfg(max_model_len: int) -> ExperimentConfig:
    cfg = ExperimentConfig()
    cfg.infra.engine.max_model_len = max_model_len
    return cfg


def test_traffic_that_fits_is_allowed() -> None:
    check_context_budget(_cfg(2048), classes=(SMALL,))


def test_traffic_that_cannot_fit_stops_the_run() -> None:
    with pytest.raises(ContextOverflowError) as exc:
        check_context_budget(_cfg(2048), classes=(SMALL, HUGE))
    assert "6144" in str(exc.value), "the message should say how big requests can get"
    assert "huge" in str(exc.value), "the message should name the offending class"
    assert "max_model_len=2048" in str(exc.value)


def test_the_default_workload_needs_more_than_2048() -> None:
    """The exact shape of Experiment1: defaults against a 2048 window."""
    with pytest.raises(ContextOverflowError):
        check_context_budget(_cfg(2048))


def test_a_window_big_enough_for_the_defaults_passes() -> None:
    check_context_budget(_cfg(6144))


def test_no_limit_configured_is_not_our_business() -> None:
    """An engine someone else started may not report one."""
    check_context_budget(_cfg(0))
