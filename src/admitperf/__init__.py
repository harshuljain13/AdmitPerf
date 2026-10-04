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
standard library. Everything else is a consumer of what it writes.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from admitperf.core import REASONS, Decision, Log, Policy, Signal, Verdict

try:
    # Read from installed metadata rather than restating it. Two copies of a version
    # number is one too many, and the stale one is always the one a report quotes.
    __version__ = version("admitperf")
except PackageNotFoundError:  # a source tree with no install
    __version__ = "0.0.0+unknown"

__all__ = [
    "REASONS",
    "Decision",
    "Log",
    "Policy",
    "Signal",
    "Verdict",
    "__version__",
]
