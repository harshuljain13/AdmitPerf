"""The null baseline.

The library ships exactly one policy: accept everything. It exists so the
harness is runnable and testable on its own, and because every comparison needs
something to beat.

Every other policy lives in the zoo (`policies/`), installed separately and
discovered through the `admitperf.policies` entry-point group. This package was
called `admitperf.policies` until the split, which collided confusingly with
that directory.
"""

from __future__ import annotations

from admitperf.baseline.no_admission import NoAdmission
from admitperf.core.registry import register

register(NoAdmission.name, NoAdmission)

__all__ = ["NoAdmission"]
