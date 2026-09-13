"""The defer queue — requests the policy asked to hold and reconsider.

DEFER is the middle verdict: not admitted, but not refused either. The runner
parks the request here and re-asks the policy later, once `retry_after_ms` has
elapsed or a fresher tick has arrived.

Ordering is FIFO in v0 (spec D11). Earliest-deadline-first is defensible and
probably better, but ordering changes results, and a benchmark should not bake
in an unexamined choice. FIFO is the honest baseline; the port lets a later
experiment swap it and attribute the difference to the swap.
"""

from __future__ import annotations

import heapq
import itertools

from admitperf.core.api import Request


class FifoDeferQueue:
    """Holds deferred requests until they are due.

    Ordering is by due time, ties broken by insertion order so the queue is
    deterministic: two runs of the same trace must pop requests in the same
    sequence, or results will not reproduce.
    """

    def __init__(self, *, max_defers: int = 100) -> None:
        if max_defers < 1:
            raise ValueError(f"max_defers must be >= 1, got {max_defers}")
        self.max_defers = max_defers
        self._heap: list[tuple[float, int, Request, int]] = []
        self._counter = itertools.count()

    def push(self, req: Request, retry_at: float, attempt: int) -> None:
        heapq.heappush(self._heap, (retry_at, next(self._counter), req, attempt))

    def due(self, now: float) -> list[tuple[Request, int]]:
        """Pop everything due at or before `now`, in order."""
        ready: list[tuple[Request, int]] = []
        while self._heap and self._heap[0][0] <= now:
            _, _, req, attempt = heapq.heappop(self._heap)
            ready.append((req, attempt))
        return ready

    def drain(self) -> list[tuple[Request, int]]:
        """Pop everything regardless of due time, for end-of-run settlement."""
        ready = [(req, attempt) for _, _, req, attempt in sorted(self._heap)]
        self._heap.clear()
        return ready

    def next_due_at(self) -> float | None:
        """When the earliest held request comes due, or None if empty.

        Lets the runner sleep exactly until there is work, instead of polling.
        """
        return self._heap[0][0] if self._heap else None

    def exhausted(self, attempt: int) -> bool:
        """True when a request has been deferred too many times.

        Without this, a policy that defers under pressure and stays under
        pressure holds a request forever. The run would appear to make progress
        while some requests silently never resolve — so the runner converts an
        exhausted defer into an explicit reject instead.
        """
        return attempt >= self.max_defers

    def __len__(self) -> int:
        return len(self._heap)


__all__ = ["FifoDeferQueue"]
