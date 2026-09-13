"""Core — the shared decision path.

Everything here is importable with no heavy dependencies (spec D7): an ops
engineer dropping AdmitPerf in front of a fleet gets this and nothing else.
`core` must never import `bench`, `engines`, or `runtime`; that rule is
enforced by tests/test_layering.py.
"""

from __future__ import annotations

from admitperf.core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)
from admitperf.core.clock import VirtualClock, WallClock
from admitperf.core.ports import (
    ENGINE_KEY,
    STATE_AGE_KEY,
    BundleWriter,
    CapabilityError,
    Clock,
    DecisionRecord,
    DeferQueue,
    EngineAdapter,
    MetricsSink,
    RequestOutcome,
    StateSource,
    TraceSource,
    check_compatibility,
)
from admitperf.core.registry import (
    available,
    get_policy,
    register,
    requirements_of,
)

__all__ = [
    "ENGINE_KEY",
    "STATE_AGE_KEY",
    "AdmissionPolicy",
    "BundleWriter",
    "CapabilityError",
    "Clock",
    "Decision",
    "DecisionKind",
    "DecisionRecord",
    "DeferQueue",
    "EngineAdapter",
    "MetricsSink",
    "Request",
    "RequestOutcome",
    "StateSource",
    "SystemState",
    "TraceSource",
    "VirtualClock",
    "WallClock",
    "available",
    "check_compatibility",
    "get_policy",
    "register",
    "requirements_of",
]
