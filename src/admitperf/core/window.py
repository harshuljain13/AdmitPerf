"""Turn cumulative counters into the change over a recent window."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterable, Mapping

#: Suffix of the derived keys, so `x_total` gains `x_total:window`.
SUFFIX = ":window"


class Window:
    """Adds `<counter>:window` = how much each counter grew over the last `seconds`.

    An engine's hit and query counters are cumulative since the engine started. A rate built on it moves less every minute the engine runs, so after an
    hour a collapse in reuse barely registers. Feed each scrape through a window and
    signals read the recent change instead:

        from admitperf.core.signals import PREFIX_COUNTERS
        window = Window(PREFIX_COUNTERS)
        policy(window.update(raw_metrics))

    The first scrape has nothing to subtract, so no `:window` key is added and a
    signal falls back to the cumulative value; `source_of` records which one it used.
    A counter that goes down means the engine restarted, so older samples are dropped.
    """

    def __init__(self, counters: Iterable[str], seconds: float = 60.0) -> None:
        if seconds <= 0:
            raise ValueError(f"seconds must be positive, got {seconds}")
        self.counters = tuple(counters)
        self.seconds = seconds
        self._samples: deque[tuple[float, dict[str, float]]] = deque()

    def update(self, metrics: Mapping[str, float], at: float | None = None) -> dict[str, float]:
        now = time.monotonic() if at is None else at
        current = {c: float(metrics[c]) for c in self.counters if c in metrics}
        if self._samples and any(v < self._samples[-1][1].get(c, v) for c, v in current.items()):
            self._samples.clear()
        while self._samples and now - self._samples[0][0] > self.seconds:
            self._samples.popleft()
        out = dict(metrics)
        if self._samples:
            oldest = self._samples[0][1]
            for c, v in current.items():
                if c in oldest:
                    out[c + SUFFIX] = v - oldest[c]
        self._samples.append((now, current))
        return out
