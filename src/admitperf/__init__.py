"""AdmitPerf — standardized admission control for LLM inference.

Bring your own infra. Your gateway already has raw metrics; AdmitPerf turns them
into signals, policies decide on signals, and every decision is recorded in a form
a report can compare across deployments.

    from admitperf import Policy
    from admitperf.core.signals import KV_PRESSURE

    class KvWall(Policy):
        name = "kv_wall"

        def decide(self, metrics):
            if KV_PRESSURE.read(metrics) >= self.threshold:
                return self.reject("kv_pressure")
            return self.admit()

`admitperf.core` is what runs in your request path and imports nothing but the
standard library. The CLI, the watcher, the report and the dashboard are consumers
of what it writes, and are not involved in serving a request.
"""

from __future__ import annotations

from admitperf.core import REASONS, Decision, Log, Policy, Signal, Verdict

__version__ = "0.1.0"

__all__ = [
    "REASONS",
    "Decision",
    "Log",
    "Policy",
    "Signal",
    "Verdict",
    "__version__",
]
