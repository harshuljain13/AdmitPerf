"""AdmitPerf — a benchmark-driven admission control layer for LLM inference.

The public surface is the frozen adapter API. Writing a policy needs only:

    from admitperf import AdmissionPolicy, Decision, Request, SystemState
"""

from __future__ import annotations

from admitperf.core import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
    available,
    get_policy,
)

__version__ = "0.0.1"

__all__ = [
    "AdmissionPolicy",
    "Decision",
    "DecisionKind",
    "Request",
    "SystemState",
    "__version__",
    "available",
    "get_policy",
]
