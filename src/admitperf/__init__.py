"""AdmitPerf — standardized admission control for LLM inference.

The contract, the policies and the report land in the PRs stacked on this one. This
commit removes the benchmark harness the package grew around, so that what replaces
it is not built next to two engine adapters, two run paths and a provisioner.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # Read from installed metadata rather than restating it here. Two copies of a
    # version number is one copy too many, and the stale one is always the one a
    # report quotes.
    __version__ = version("admitperf")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
