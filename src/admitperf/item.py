"""One of the survey's seven reporting items, checked against a run."""

from __future__ import annotations

from dataclasses import dataclass

from admitperf.status import Status


@dataclass(frozen=True)
class Item:
    number: int
    name: str
    status: Status
    detail: str
    #: The survey's own wording, so a reader is shown what the item asks for rather
    #: than a paraphrase that has drifted into something subtly different.
    asks: str

    @property
    def passed(self) -> bool:
        return self.status is Status.OK
