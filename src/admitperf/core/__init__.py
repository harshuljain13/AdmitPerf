"""Core — the decision path.

The four objects (Request, SystemState, Decision, Outcome), the policy base
class, the policy registry, and the cached state a policy reads. No engine, no
provisioning, no benchmark machinery.
"""

from __future__ import annotations

from admitperf.core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from admitperf.core.ports import (
    STATE_AGE_KEY,
    CapabilityError,
    DecisionRecord,
    EngineAdapter,
    RequestOutcome,
    check_compatibility,
)
from admitperf.core.registry import available, get_policy, register, requirements_of
from admitperf.core.state import StateCache, empty_state, state_age

__all__ = [
    "STATE_AGE_KEY",
    "AdmissionPolicy",
    "CapabilityError",
    "Decision",
    "DecisionKind",
    "DecisionRecord",
    "EngineAdapter",
    "Request",
    "RequestOutcome",
    "StateCache",
    "SystemState",
    "available",
    "check_compatibility",
    "empty_state",
    "get_policy",
    "register",
    "requirements_of",
    "state_age",
]
