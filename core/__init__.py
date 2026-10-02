"""Core — the decision path, and the public surface.

The four objects (Request, SystemState, Decision, Outcome), the policy base
class, the policy registry, and the cached state a policy reads. No engine
provisioning, no benchmark machinery.

Writing a policy needs only:

    from core import AdmissionPolicy, Decision, Request, SystemState
"""

from __future__ import annotations

from core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from core.ports import (
    STATE_AGE_KEY,
    CapabilityError,
    DecisionRecord,
    EngineAdapter,
    RequestOutcome,
    check_compatibility,
)
from core.registry import available, get_policy, register, requirements_of
from core.state import StateCache, empty_state, state_age

__version__ = "0.0.1"

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
    "__version__",
    "available",
    "check_compatibility",
    "empty_state",
    "get_policy",
    "register",
    "requirements_of",
    "state_age",
]
