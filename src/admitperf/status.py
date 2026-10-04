"""Whether a run evidences one of the survey's reporting items."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """Three outcomes, and the third is the important one.

    UNEVIDENCED is not a failure of the run — it means this log does not carry the fact
    the item asks for. Collapsing it into FAIL would punish a host for not recording
    something; collapsing it into OK would be a lie. The survey's finding is that the
    corpus is silent on these, so silence has to be its own state.
    """

    OK = "ok"
    FAIL = "fail"
    UNEVIDENCED = "unevidenced"
