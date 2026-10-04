"""The policies AdmitPerf ships.

Primitives, not reproductions of published work. Each is something most gateways
actually want, and together they cover the shapes the survey's signal-structure
axis names: a scalar threshold, and a dual gate.

Your own policy is an equal citizen — same base class, same records, same report.
Nothing here is privileged.
"""

from __future__ import annotations

from admitperf.policies.dual_gate import DualGate
from admitperf.policies.kv_threshold import KvThreshold
from admitperf.policies.no_admission import NoAdmission
from admitperf.policies.queue_depth import QueueDepth

__all__ = ["DualGate", "KvThreshold", "NoAdmission", "QueueDepth"]
