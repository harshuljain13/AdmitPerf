"""Admission-control policies — pluggable baselines.

Register new policies in POLICIES so the CLI can resolve them by name.
"""

from __future__ import annotations

from admitperf.policies.base import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from admitperf.policies.no_admission import NoAdmission

POLICIES: dict[str, type[AdmissionPolicy]] = {
    NoAdmission.name: NoAdmission,
}


def get_policy(name: str) -> AdmissionPolicy:
    if name not in POLICIES:
        raise KeyError(f"unknown policy {name!r}; registered: {sorted(POLICIES)}")
    return POLICIES[name]()


__all__ = [
    "AdmissionPolicy",
    "Decision",
    "DecisionKind",
    "NoAdmission",
    "POLICIES",
    "Request",
    "SystemState",
    "get_policy",
]
