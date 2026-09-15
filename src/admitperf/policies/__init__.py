"""Built-in policies.

The library ships one: a null baseline, so the harness is runnable and
testable on its own. Everything else lives in the zoo (`policies/`), installed
separately and discovered through the `admitperf.policies` entry-point group.

The frozen adapter API lives in ``admitperf.core.api`` and the registry in
``admitperf.core.registry``; both are re-exported here for pre-split imports.
"""

from __future__ import annotations

from admitperf.core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from admitperf.core.registry import available, get_policy, register
from admitperf.policies.no_admission import NoAdmission

register(NoAdmission.name, NoAdmission)

#: Built-ins only. Prefer ``admitperf.core.registry.available()``, which also
#: includes anything installed as a plugin.
POLICIES: dict[str, type[AdmissionPolicy]] = {NoAdmission.name: NoAdmission}

__all__ = [
    "POLICIES",
    "AdmissionPolicy",
    "Decision",
    "DecisionKind",
    "NoAdmission",
    "Request",
    "SystemState",
    "available",
    "get_policy",
    "register",
]
