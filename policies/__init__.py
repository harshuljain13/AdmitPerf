"""Admission-control policies.

See README.md in this directory for what each one decides on, what signal it
needs, and which regime it suits — that is the catalogue, and it exists so
nobody has to read the source to answer those questions.

Policies shipped here are registered below. A policy in *your own* package
registers through the ``admitperf.policies`` entry-point group instead, with no
edit to this file; see ../../../CONTRIBUTING.md.
"""

from __future__ import annotations

from core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from core.registry import available, get_policy, register
from policies.chronos import ChronosInspiredWCRT
from policies.kv_threshold import KVThreshold
from policies.no_admission import NoAdmission
from policies.queue_depth import QueueDepth, QueueDepthDefer

for _policy in (NoAdmission, KVThreshold, QueueDepth, QueueDepthDefer, ChronosInspiredWCRT):
    register(_policy.name, _policy)

#: Built-ins. Prefer ``admitperf.core.registry.available()``, which also
#: includes anything installed as a plugin.
POLICIES: dict[str, type[AdmissionPolicy]] = {
    p.name: p for p in (NoAdmission, KVThreshold, QueueDepth, QueueDepthDefer, ChronosInspiredWCRT)
}

__all__ = [
    "POLICIES",
    "AdmissionPolicy",
    "ChronosInspiredWCRT",
    "Decision",
    "DecisionKind",
    "KVThreshold",
    "NoAdmission",
    "QueueDepth",
    "QueueDepthDefer",
    "Request",
    "SystemState",
    "available",
    "get_policy",
    "register",
]
