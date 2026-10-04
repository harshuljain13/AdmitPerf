"""Where decisions get written."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

#: Records buffered before dropping. A few hundred bytes each, so single-digit
#: megabytes: small enough never to matter, large enough to ride out a disk stall.
CAPACITY = 10_000


class Log:
    """Append JSON Lines from a background thread.

    This sits in a production request path, so every choice here is about blast
    radius rather than throughput:

      - A background thread, so a slow disk adds no latency to a decision.
      - A bounded buffer, because an unbounded one behind a slow writer is a memory
        leak that takes the gateway down — far worse than losing records.
      - When full, the OLDEST record is dropped. During the saturation event you are
        recording, the recent records are the ones describing it.
      - `dropped` is counted and surfaced, so a report says "2,104 records lost"
        instead of quietly describing a sample as the whole.
      - Nothing here raises. A logging fault must not become a 500 on a request that
        was going to be admitted.

    `Log(None)` writes nowhere, so constructing a policy never touches a disk.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._dropped = 0
        self._lock = threading.Lock()
        self._closed = False
        self._q: queue.Queue[dict[str, Any] | None] | None = None
        self._thread: threading.Thread | None = None
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._q = queue.Queue(maxsize=CAPACITY)
        self._thread = threading.Thread(target=self._drain, name="admitperf-log", daemon=True)
        self._thread.start()

    def write(self, record: dict[str, Any]) -> None:
        if self._q is None or self._closed:
            return
        try:
            self._q.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            try:
                self._q.get_nowait()
                self._q.put_nowait(record)
            except (queue.Empty, queue.Full):
                pass

    def close(self) -> None:
        if self._q is None or self._closed:
            return
        self._closed = True
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def _drain(self) -> None:
        assert self.path is not None and self._q is not None
        with self.path.open("a", buffering=1) as fh:
            while True:
                item = self._q.get()
                if item is None:
                    return
                try:
                    fh.write(json.dumps(item, default=str) + "\n")
                except Exception:  # noqa: BLE001 - never reaches the request path
                    with self._lock:
                        self._dropped += 1

    @staticmethod
    def read(path: str | Path) -> list[dict[str, Any]]:
        """Every record in a log, skipping a truncated final line — which is what a
        gateway that was killed rather than closed leaves behind."""
        out: list[dict[str, Any]] = []
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
