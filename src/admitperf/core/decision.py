"""One policy's answer."""

from __future__ import annotations

from admitperf.core.reasons import ADMITTED, REASONS
from admitperf.core.verdict import Verdict


class Decision:
    """A verdict, a reason from the vocabulary, and the response it implies."""

    __slots__ = ("reason", "retry_after_ms", "verdict")

    def __init__(
        self,
        verdict: Verdict,
        reason: str | None = None,
        retry_after_ms: int | None = None,
    ) -> None:
        if verdict is Verdict.ADMIT:
            if reason is not None:
                raise ValueError("an admitted request has no refusal reason")
        elif reason not in REASONS:
            raise ValueError(
                f"reason {reason!r} is not in the vocabulary. A report groups by it, "
                f"so it cannot be free text. Use one of: {', '.join(sorted(REASONS))}"
            )
        self.verdict = verdict
        self.reason = reason
        self.retry_after_ms = retry_after_ms

    @property
    def admitted(self) -> bool:
        """The question a gateway is actually asking.

        Not `verdict is REJECT`: a gateway with no queue cannot honour a defer and
        should not need to import an enum to discover that.
        """
        return self.verdict is Verdict.ADMIT

    @property
    def status(self) -> int:
        """Derived, never stored, so it cannot drift from the reason."""
        return ADMITTED if self.admitted else REASONS[self.reason]

    def __repr__(self) -> str:
        return f"Decision({self.verdict.value}, {self.reason!r}, status={self.status})"
