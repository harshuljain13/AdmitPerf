"""Tasks 1.4 / 1.5 — third-party extensibility (D3) and capability checks (D8).

The headline guarantee (requirements G3): a policy shipped in someone else's
pip package resolves by name with **zero edits** to src/admitperf/. Before the
entry-point registry, that was impossible — POLICIES was a dict literal in
installed source.
"""

from __future__ import annotations

import pytest

from admitperf.core.api import AdmissionPolicy, Decision, DecisionKind, Request, SystemState
from admitperf.core.ports import CapabilityError, check_compatibility
from admitperf.core.registry import available, get_policy, register, requirements_of

thirdparty = pytest.importorskip(
    "thirdparty_policy",
    reason="fixture package not installed: pip install -e tests/fixtures/thirdparty_policy",
)


def test_builtins_are_registered() -> None:
    names = available()
    assert "no_admission" in names
    assert "kv_threshold" in names


def test_third_party_policy_is_discovered() -> None:
    """G3 — discovered purely via the admitperf.policies entry-point group."""
    assert "third_party_reject_all" in available()


def test_third_party_policy_resolves_and_runs() -> None:
    policy = get_policy("third_party_reject_all")
    assert isinstance(policy, AdmissionPolicy)
    assert type(policy).__module__ == "thirdparty_policy"

    decision = policy.decide(_request(), _state())
    assert decision.kind is DecisionKind.REJECT
    assert decision.reason == "third_party_says_no"


def test_unknown_policy_lists_what_is_available() -> None:
    with pytest.raises(KeyError) as exc:
        get_policy("nope")
    assert "no_admission" in str(exc.value)


def test_duplicate_builtin_registration_is_rejected() -> None:
    class Impostor(AdmissionPolicy):
        name = "no_admission"

        def decide(self, req: Request, state: SystemState) -> Decision:
            return Decision.admit()

    with pytest.raises(ValueError, match="already registered"):
        register("no_admission", Impostor)


def test_policy_constructor_kwargs_pass_through() -> None:
    policy = get_policy("kv_threshold", threshold=0.5)
    assert policy.threshold == 0.5  # type: ignore[attr-defined]


# --- capability checking (D8) -------------------------------------------------


def test_policy_declares_its_requirements() -> None:
    assert requirements_of(get_policy("kv_threshold")) == frozenset({"kv_used_fraction"})
    assert requirements_of(get_policy("no_admission")) == frozenset()


def test_incompatible_wiring_fails_fast_and_names_both_sides() -> None:
    with pytest.raises(CapabilityError) as exc:
        check_compatibility(
            policy_name="kv_threshold",
            requires=frozenset({"kv_used_fraction"}),
            engine_name="metrics_free_engine",
            provides=frozenset({"waiting_requests"}),
        )
    message = str(exc.value)
    assert "kv_threshold" in message
    assert "metrics_free_engine" in message
    assert "kv_used_fraction" in message


def test_compatible_wiring_passes() -> None:
    check_compatibility(
        policy_name="kv_threshold",
        requires=frozenset({"kv_used_fraction"}),
        engine_name="replay",
        provides=frozenset({"kv_used_fraction", "waiting_requests"}),
    )


def _state(kv: float = 0.2) -> SystemState:
    return SystemState(
        now=0.0,
        kv_used_fraction=kv,
        running_requests=1,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )


def _request() -> Request:
    return Request(request_id="r1", tenant_id="t1", arrival_time=0.0, input_tokens=100)


def test_an_unknown_setting_names_the_policy_and_its_parameters() -> None:
    """Configs outlive the policies they configure. A bare TypeError from a
    constructor names neither, and the usual cause is a config written against
    an older version."""
    with pytest.raises(TypeError) as exc:
        get_policy("kv_threshold", not_a_real_setting=1)

    message = str(exc.value)
    assert "kv_threshold" in message
    assert "threshold" in message  # the parameter it does accept
