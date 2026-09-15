"""Smoke tests for the frozen adapter API v0."""

import pytest

from admitperf import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
    __version__,
    get_policy,
)
from admitperf.baseline import NoAdmission


def test_version() -> None:
    assert __version__ == "0.0.1"


def test_decision_kinds() -> None:
    assert DecisionKind.ADMIT.value == "admit"
    assert DecisionKind.DEFER.value == "defer"
    assert DecisionKind.REJECT.value == "reject"


def test_decision_constructors() -> None:
    assert Decision.admit().kind is DecisionKind.ADMIT
    assert Decision.defer(retry_after_ms=50).retry_after_ms == 50
    assert Decision.reject("kv_pressure").reason == "kv_pressure"


def test_decision_validation() -> None:
    with pytest.raises(ValueError):
        Decision(DecisionKind.DEFER)  # missing retry_after_ms
    with pytest.raises(ValueError):
        Decision(DecisionKind.REJECT)  # missing reason


def test_policy_is_abstract() -> None:
    with pytest.raises(TypeError):
        AdmissionPolicy()  # type: ignore[abstract]


def _sample_state() -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=0.2,
        running_requests=1,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )


def _sample_request() -> Request:
    return Request(
        request_id="r1",
        tenant_id="t1",
        arrival_time=0.0,
        input_tokens=100,
    )


def test_no_admission_admits_everything() -> None:
    p = NoAdmission()
    d = p.decide(_sample_request(), _sample_state())
    assert d.kind is DecisionKind.ADMIT


def test_registry_lookup() -> None:
    p = get_policy("no_admission")
    assert isinstance(p, NoAdmission)
    with pytest.raises(KeyError):
        get_policy("does_not_exist")
