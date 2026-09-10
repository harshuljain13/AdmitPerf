"""Smoke tests — the scaffold at least imports."""

from admitbench import __version__
from admitbench.policies import AdmissionDecision, AdmissionPolicy


def test_version() -> None:
    assert __version__ == "0.0.1"


def test_decision_enum() -> None:
    assert AdmissionDecision.ADMIT.value == "admit"
    assert AdmissionDecision.QUEUE.value == "queue"
    assert AdmissionDecision.REJECT.value == "reject"


def test_policy_is_abstract() -> None:
    try:
        AdmissionPolicy()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("AdmissionPolicy should be abstract")
