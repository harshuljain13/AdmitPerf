"""Built-in admission-control policies.

The frozen adapter API lives in ``admitperf.core.api`` and the registry in
``admitperf.core.registry``; both are re-exported here so that pre-split
imports keep working.

To add a policy *inside* this package, import it below and call ``register``.
To add one from your **own** package, use the ``admitperf.policies`` entry-point
group — no edit to this file is needed (spec D3). See CONTRIBUTING.md.
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
from admitperf.policies.kv_threshold import KVThreshold
from admitperf.policies.no_admission import NoAdmission
from admitperf.policies.queue_depth import QueueDepth, QueueDepthDefer

register(NoAdmission.name, NoAdmission)
register(KVThreshold.name, KVThreshold)
register(QueueDepth.name, QueueDepth)
register(QueueDepthDefer.name, QueueDepthDefer)

#: Built-in policies. Retained for backward compatibility; prefer
#: ``admitperf.core.registry.available()``, which also includes plugins.
POLICIES: dict[str, type[AdmissionPolicy]] = {
    NoAdmission.name: NoAdmission,
    KVThreshold.name: KVThreshold,
    QueueDepth.name: QueueDepth,
    QueueDepthDefer.name: QueueDepthDefer,
}

__all__ = [
    "POLICIES",
    "AdmissionPolicy",
    "Decision",
    "DecisionKind",
    "KVThreshold",
    "QueueDepth",
    "QueueDepthDefer",
    "NoAdmission",
    "Request",
    "SystemState",
    "available",
    "get_policy",
    "register",
]
