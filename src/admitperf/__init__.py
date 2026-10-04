"""AdmitPerf — standardized admission control for LLM inference.

The contract, the policies and the report land in the following PRs. This commit
removes the benchmark harness this package grew around, so that what replaces it
is not built next to two engine adapters, two run paths and a provisioner.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
