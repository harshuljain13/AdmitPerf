"""The contract: metrics in, signals derived, policies decide.

This subpackage is the part that runs in your request path, so it holds the line
that makes AdmitPerf safe to install: no third-party imports, no sockets, no
subprocesses, no filesystem beyond the log you asked for. A test enforces each.

Everything else in the package — the CLI, the watcher, the report, the dashboard —
is a consumer of what this produces and may do as it likes.
"""

from __future__ import annotations

from admitperf.core.decision import Decision
from admitperf.core.log import Log
from admitperf.core.policy import Policy
from admitperf.core.reasons import ADMITTED, REASONS
from admitperf.core.signal import Signal, Source
from admitperf.core.verdict import Verdict

__all__ = [
    "ADMITTED",
    "REASONS",
    "Decision",
    "Log",
    "Policy",
    "Signal",
    "Source",
    "Verdict",
]
