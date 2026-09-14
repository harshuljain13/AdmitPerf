"""Backward-compatible re-export of the frozen adapter API.

The API now lives in ``admitperf.core.api`` (spec D7: ``core`` owns the shared
decision path). This module is kept so that existing imports — and any policy
written against the pre-split layout — keep working unchanged.

New code should import from ``admitperf`` or ``admitperf.core.api``.
"""

from __future__ import annotations

from admitperf.core.api import (
    AdmissionPolicy,
    Decision,
    DecisionKind,
    Request,
    SystemState,
)

__all__ = [
    "AdmissionPolicy",
    "Decision",
    "DecisionKind",
    "Request",
    "SystemState",
]
