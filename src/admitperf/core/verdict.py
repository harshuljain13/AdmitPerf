"""What a policy can decide."""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """Admit, hold, or refuse.

    DEFER exists because "come back in 200ms" is a different instruction to a
    caller than "no". A gateway with no queue may treat it as a refusal, which is
    why `Decision.admitted` is the question to ask rather than the verdict itself.
    """

    ADMIT = "admit"
    DEFER = "defer"
    REJECT = "reject"
