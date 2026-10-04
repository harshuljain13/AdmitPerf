"""Record what your cluster was doing, with no code in your request path."""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from admitperf.core.log import Log
from admitperf.core.signals import ALL

#: name{labels} value — the Prometheus text format, enough of it.
_LINE = re.compile(r"^(?P<name>[a-zA-Z_:][\w:]*)(?:\{[^}]*\})?\s+(?P<value>[-+0-9.eE]+)\s*$")


class Watch:
    """Poll a metrics endpoint and write a log AdmitPerf can report on.

    The zero-integration on-ramp. Point it at a `/metrics` URL you already expose
    and you get the one question no surveyed paper answers — *could a policy have
    fired here, and which signal actually moved* — without touching your gateway.

        admitperf watch http://localhost:8000/metrics --for 1h -o trace.jsonl

    This is a CLI tool, not part of `admitperf.core`. The library never fetches
    anything, because a network round trip has no business on an admission
    decision; a watcher running out of band is a different matter entirely.

    A failed scrape is recorded as a failure, never as zeros. Zeros read as a
    completely idle cluster, which would make the whole trace look like headroom.
    """

    def __init__(self, url: str, out: str | Path, *, interval: float = 1.0) -> None:
        self.url = url
        self.interval = interval
        self.log = Log(out)
        self.samples = 0
        self.failures = 0

    def once(self) -> dict[str, float] | None:
        """One scrape. None when it failed."""
        try:
            with urllib.request.urlopen(self.url, timeout=5.0) as resp:
                text = resp.read().decode()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.log.write({"at": time.time(), "scrape_error": str(exc)})
            self.failures += 1
            return None

        metrics = parse(text)
        self.log.write(
            {
                "at": time.time(),
                "metrics": metrics,
                "signals": {s.name: s.read(metrics) for s in ALL},
            }
        )
        self.samples += 1
        return metrics

    def run(self, seconds: float) -> Watch:
        """Scrape until the clock runs out.

        Sleeps against an absolute schedule, not a cumulative one: sleeping
        `interval` between scrapes drifts slower than the stated rate by whatever
        each scrape costs, and then the sample interval in the log is a lie.
        """
        started = time.perf_counter()
        n = 0
        try:
            while time.perf_counter() - started < seconds:
                self.once()
                n += 1
                due = started + n * self.interval
                gap = due - time.perf_counter()
                if gap > 0:
                    time.sleep(gap)
        finally:
            self.log.close()
        return self


def parse(text: str) -> dict[str, float]:
    """Prometheus exposition text to name -> value.

    Labels are dropped and same-named series summed. That is right for counts
    across replicas and wrong for a fraction, which is why a signal whose metric
    carries labels should be given an explicit source by the host rather than
    inferred from a sum.
    """
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        name = m.group("name")
        out[name] = out.get(name, 0.0) + value if name in out else value
    return out
