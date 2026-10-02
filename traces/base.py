"""Base interfaces for trace loaders."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class TraceEvent:
    """One event in a replayable trace.

    An event is either the arrival of a new request or the next turn of an
    ongoing agent session. Agent sessions are identified by agent_id; a chat
    request has agent_id == request_id (session of one).
    """

    time: float
    request_id: str
    agent_id: str
    tenant_id: str
    input_tokens: int
    expected_output_tokens: int | None
    turn_index: int
    is_final_turn: bool


class TraceLoader(ABC):
    """Abstract trace source. Implementations wrap Azure, LMSYS, SWE-bench, etc."""

    name: str

    @abstractmethod
    def events(self) -> Iterator[TraceEvent]: ...

    @abstractmethod
    def total_events(self) -> int: ...
