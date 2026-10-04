"""A quantity a policy decides on, and how to read it from raw metrics."""

from __future__ import annotations

from collections.abc import Callable, Mapping

#: A raw metric name, or a function over the raw metrics.
Source = str | Callable[[Mapping[str, float]], float]


class Signal:
    """Bridges your metrics and a policy's input.

    Your infra has raw metrics. A policy needs `kv_pressure`. This is the one
    object in between: a name, the sources that can produce it, and a sanity range.

        KV_PRESSURE.read(your_scrape)   ->  0.93

    The conversion lives here rather than in each gateway on purpose. If every host
    computed `kv_pressure` itself, two of them reporting 0.93 would mean different
    things and the shared name would be worth nothing — which is precisely what the
    survey found across sixteen papers.
    """

    def __init__(
        self,
        name: str,
        sources: list[Source] | None = None,
        *,
        lo: float | None = None,
        hi: float | None = None,
        help: str = "",
    ) -> None:
        self.name = name
        #: Tried in order, so a value the engine reports directly comes before
        #: anything derived — a derivation only adds assumptions.
        self.sources: list[Source] = list(sources or [])
        self.lo = lo
        self.hi = hi
        self.help = help

    def add_source(self, source: Source) -> Signal:
        """Teach this signal to read your stack; yours is tried first.

        How a host whose metric is named something else participates. No subclass,
        no plugin, no registration call.

            KV_PRESSURE.add_source("acme.cache.used_frac")
            KV_PRESSURE.add_source(lambda m: m["used"] / m["total"])
        """
        self.sources.insert(0, source)
        return self

    def read(self, metrics: Mapping[str, float]) -> float | None:
        """The value, or None when nothing here supplies it.

        None, never 0.0. A KV pressure of zero claims the cache is empty, which
        reads as headroom — so a policy would admit everything while appearing to
        work, and score exactly like no policy at all.
        """
        found = self._read(metrics)
        return None if found is None else found[0]

    def source_of(self, metrics: Mapping[str, float]) -> str | None:
        """Which source produced the value. Recorded, so two reports that resolved
        the same signal differently are distinguishable rather than silently
        incomparable."""
        found = self._read(metrics)
        return None if found is None else found[1]

    def _read(self, metrics: Mapping[str, float]) -> tuple[float, str] | None:
        for source in self.sources:
            try:
                value = (
                    float(metrics[source]) if isinstance(source, str) else float(source(metrics))
                )
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            if not self._sane(value):
                # Out of range means the source is wrong, not that the cluster is
                # saturated. Skipped rather than clamped: clamping turns a mis-wired
                # metric into a plausible number, which is the worse failure.
                continue
            label = source if isinstance(source, str) else getattr(source, "__name__", "fn")
            return value, label
        return None

    def _sane(self, value: float) -> bool:
        if self.lo is not None and value < self.lo:
            return False
        return not (self.hi is not None and value > self.hi)

    def __repr__(self) -> str:
        return f"Signal({self.name!r})"
