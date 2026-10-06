"""Send traffic at a fixed arrival rate, and record what each request got."""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from admitperf.core.log import Log

#: Concurrent requests allowed in flight. This is NOT a concurrency target — it is a
#: backstop so a stalled engine cannot spawn threads without limit. Hitting it means
#: the arrival rate outran the engine by more than this, which is reported rather
#: than silently throttled: a capped generator is a closed loop wearing a disguise.
IN_FLIGHT_CAP = 1024

#: Opening guess at tokens per generated word, replaced by a measurement against the
#: engine before any traffic is sent. Never trusted on its own: the filler here is
#: random six-digit numbers, which Qwen2.5 splits one token per DIGIT at about 7.0
#: tokens per word. A hardcoded 1.3 asked for 8192 tokens and built 32,765 — over the
#: 32768 context, so every request 400'd. Vocabularies differ per model; measuring
#: costs one HTTP call.
TOKENS_PER_WORD = 7.0

#: Rounds of refinement against /tokenize. Converges in two or three; the cap is so a
#: model whose tokenizer this cannot approximate fails loudly rather than looping.
CALIBRATION_ROUNDS = 6

#: How close the calibrated prompt must land, as a fraction of the target.
CALIBRATION_TOLERANCE = 0.02

#: Consecutive failures with nothing served before a run gives up. Low enough to
#: catch a wrong endpoint in seconds, high enough that a cold engine refusing the
#: first few requests does not abort a legitimate run.
ABORT_AFTER = 20


class Load:
    """Offer requests at `rps` and write one record per request.

    Four properties decide whether a run can mean anything, and all four are easy to
    get wrong in a way that still produces a tidy-looking report:

    **Open loop.** Arrivals are scheduled by the clock, not by completions. A
    generator holding N requests in flight and starting a new one as each finishes
    cannot overload anything — it slows down exactly as much as the server does, the
    queue never grows, and admission control has nothing to refuse. Overload means
    arrivals outpacing service, which requires arrivals that do not wait.

    **Unique prompts.** `single.yaml` enables prefix caching. Sending the same prompt
    twice makes the second one nearly free and leaves no KV footprint, so a load test
    built from one repeated prompt reports a cache that never fills no matter the
    rate. Every prompt here starts with its own random text.

    **`ignore_eos`.** Without it the model stops at its own end-of-sequence token,
    often after a few dozen tokens rather than the requested hundreds. Decode time
    collapses by an order of magnitude, each request holds its cache for a fraction
    as long, and the cache occupancy under test is not the one you asked for.

    **Time to first token, measured by streaming.** TTFT is the latency admission
    control exists to protect, and it is unobservable without streaming: a
    non-streamed response arrives once, so the only thing measurable is end-to-end,
    which mixes queueing with generation length.

    This is a CLI tool. It is not part of `admitperf.core` and nothing in the request
    path imports it.
    """

    def __init__(
        self,
        url: str,
        out: str | Path | None,
        *,
        model: str,
        rps: float,
        prompt_tokens: int = 8192,
        output_tokens: int = 512,
        seed: int = 0,
        timeout: float = 600.0,
        endpoint: str = "chat",
    ) -> None:
        if endpoint not in ("chat", "completions"):
            raise ValueError(f"endpoint must be 'chat' or 'completions', not {endpoint!r}")
        self.endpoint = endpoint
        self.url = url.rstrip("/")
        self.log = Log(out)
        self.model = model
        self.rps = rps
        self.prompt_tokens = prompt_tokens
        self.output_tokens = output_tokens
        self.timeout = timeout
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._in_flight = 0

        self.sent = 0
        self.ok = 0
        self.refused = 0  #: 503 and 429 — a gateway turning work away, not an error
        self.failed = 0
        self.at_capacity = 0
        self.prompt_tokens_seen: list[int] = []
        #: Fitted to the engine's tokenizer by `calibrate()`, which `run()` calls.
        self.tokens_per_word = TOKENS_PER_WORD
        #: What /tokenize counted for the calibration prompt; None if it would not say.
        self.calibrated: int | None = None
        #: Why the run gave up early, if it did. Set once and surfaced by the caller.
        self.aborted: str | None = None

    # -- the prompt ---------------------------------------------------------

    def prompt(self, request_id: str) -> str:
        """A prompt of roughly `prompt_tokens`, unique to this request.

        Seeded from the request id so a rerun of the same seed sends the same text,
        while no two requests in a run share a prefix.
        """
        rng = random.Random(f"{self.model}:{request_id}")
        words = max(1, int(self.prompt_tokens / self.tokens_per_word))
        return " ".join(str(rng.randrange(100_000, 999_999)) for _ in range(words))

    def count_tokens(self, text: str) -> int | None:
        """What the engine's own tokenizer makes of this text, or None if it won't say.

        vLLM serves /tokenize. An engine without it leaves the ratio at its guess, and
        the run reports the prompt length it actually achieved either way.
        """
        body = json.dumps({"model": self.model, "prompt": text}).encode()
        req = urllib.request.Request(
            f"{self.url}/tokenize", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                return int(json.load(resp)["count"])
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError):
            return None

    def calibrate(self) -> float:
        """Fit `tokens_per_word` to this engine's tokenizer before sending traffic.

        The prompt length is the single number that decides whether a KV-pressure
        policy can fire — below the crossover the scheduler binds first and no
        threshold is reachable at any arrival rate. Guessing it wrong does not fail
        loudly: it produces a run with tidy latencies and a cache that never fills.

        Measuring is one HTTP call per round and converges in two or three.
        """
        for _ in range(CALIBRATION_ROUNDS):
            text = self.prompt("calibrate")
            count = self.count_tokens(text)
            if count is None:
                self.calibrated = None
                return self.tokens_per_word
            self.calibrated = count
            if abs(count - self.prompt_tokens) <= self.prompt_tokens * CALIBRATION_TOLERANCE:
                return self.tokens_per_word
            self.tokens_per_word = count / max(1, len(text.split()))
        return self.tokens_per_word

    # -- one request -------------------------------------------------------

    def once(self, request_id: str) -> dict[str, object]:
        """Issue one streaming completion and record what happened to it."""
        text = self.prompt(request_id)
        payload = {
            "model": self.model,
            "max_tokens": self.output_tokens,
            # Generate the full requested length. See the class docstring.
            "ignore_eos": True,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.0,
        }
        # Chat by default. vLLM 0.31 serving Qwen2.5-7B-Instruct answers
        # /v1/chat/completions and returns 404 for /v1/completions, so defaulting to
        # the legacy route cost a three-minute run that recorded 404s at the right
        # arrival rate — a load test that looks like it worked.
        if self.endpoint == "chat":
            path = "/v1/chat/completions"
            payload["messages"] = [{"role": "user", "content": text}]
        else:
            path = "/v1/completions"
            payload["prompt"] = text

        req = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

        started = time.monotonic()
        record: dict[str, object] = {"request_id": request_id, "at": time.time()}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ttft = None
                tokens = 0
                for raw in resp:
                    line = raw.decode().strip()
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk == "[DONE]":
                        break
                    event = json.loads(chunk)
                    # The usage-only final chunk carries no choices, and a chat stream
                    # opens with a role-only delta that holds no text.
                    for choice in event.get("choices") or []:
                        piece = choice.get("text") or (choice.get("delta") or {}).get("content")
                        if not piece:
                            continue
                        if ttft is None:
                            ttft = (time.monotonic() - started) * 1000.0
                        tokens += 1
                    if usage := event.get("usage"):
                        record["prompt_tokens"] = usage.get("prompt_tokens")
                record |= {
                    "status": resp.status,
                    "ttft_ms": ttft,
                    "latency_ms": (time.monotonic() - started) * 1000.0,
                    "output_tokens": tokens,
                }
        except urllib.error.HTTPError as exc:
            # A refusal is an outcome, not a failure. Admission control produces
            # these on purpose, and counting them as errors would make a working
            # policy look like a broken load generator.
            record |= {
                "status": exc.code,
                "latency_ms": (time.monotonic() - started) * 1000.0,
                "refused": exc.code in (429, 503),
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            record |= {"error": str(exc), "latency_ms": (time.monotonic() - started) * 1000.0}

        self.log.write(record)
        return record

    # -- the run -----------------------------------------------------------

    def run(self, seconds: float) -> Load:
        """Offer traffic for `seconds`, then wait for what is still in flight.

        Arrival gaps are exponential, so requests bunch the way real ones do. A
        fixed gap produces a smoothness no cluster ever sees and understates queueing.
        """
        # Before any traffic: the prompt has to be the length that was asked for, or
        # the experiment is about a different workload than the one being reported.
        self.calibrate()

        threads: list[threading.Thread] = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(self._rng.expovariate(self.rps))
            # Stop rather than spend the window recording the same error. A wrong
            # endpoint produced 300 identical 404s at exactly the requested arrival
            # rate, which reads as a load test that ran.
            if self.aborted is not None:
                break
            if self.failed >= ABORT_AFTER and self.ok == 0 and self.refused == 0:
                self.aborted = (
                    f"{self.failed} requests failed and none succeeded — "
                    "wrong --model, wrong --endpoint, or the engine is not serving"
                )
                break
            with self._lock:
                if self._in_flight >= IN_FLIGHT_CAP:
                    self.at_capacity += 1
                    continue
                self._in_flight += 1
            self.sent += 1
            t = threading.Thread(target=self._work, args=(f"r{self.sent:06d}",), daemon=True)
            t.start()
            threads.append(t)

        # Requests outlive the arrival window; counting only what finished inside it
        # would drop the slowest ones, which are the ones the experiment is about.
        for t in threads:
            t.join(timeout=self.timeout + 60)
        self.log.close()
        return self

    def _work(self, request_id: str) -> None:
        try:
            record = self.once(request_id)
        finally:
            with self._lock:
                self._in_flight -= 1
        if record.get("refused"):
            self.refused += 1
        elif record.get("status") == 200:
            self.ok += 1
            if seen := record.get("prompt_tokens"):
                self.prompt_tokens_seen.append(int(seen))
        else:
            self.failed += 1
