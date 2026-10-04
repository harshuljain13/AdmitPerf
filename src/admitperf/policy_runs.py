"""One policy inside one experiment, and every run of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PolicyRuns:
    """A policy under test, with its runs.

    Named for what it holds. This was `Arm`, borrowed from clinical trials, which told a
    reader nothing: the thing on screen is a policy, and the spread beside it comes from
    running that policy more than once.
    """

    experiment: str
    policy: str
    baseline: bool
    logs: list[Path] = field(default_factory=list)

    @property
    def label(self) -> str:
        """How this appears in a menu or a chart: the policy, and how many runs.

        Never the file path. A path says where bytes live, not what was measured, and it
        changes when someone tidies a directory.
        """
        suffix = f" ×{len(self.logs)}" if len(self.logs) > 1 else ""
        return f"{self.policy}{suffix}" + ("  (baseline)" if self.baseline else "")
