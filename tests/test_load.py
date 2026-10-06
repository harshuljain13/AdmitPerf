"""The load generator, against a server that streams the way vLLM does.

These assertions are about the four properties that decide whether a run can mean
anything — open loop, unique prompts, ignore_eos, streamed TTFT. Each of them is
invisible in a report: get one wrong and the numbers still look reasonable.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from admitperf.load import Load

#: Every request body the fake engine received, for inspecting what was asked for.
RECEIVED: list[dict[str, Any]] = []


def prompt_of(body: dict[str, Any]) -> str:
    """The prompt text, whichever route carried it."""
    if "prompt" in body:
        return str(body["prompt"])
    return str(body["messages"][0]["content"])


class FakeEngine(BaseHTTPRequestHandler):
    """Streams SSE chunks then a usage-only final chunk, as vLLM does."""

    refuse = False

    #: Tokens per word this fake claims, so a test can prove calibration converges on
    #: a ratio it did not start with.
    ratio = 7.0

    #: Set to simulate an engine with no /tokenize route.
    no_tokenizer = False

    def do_POST(self) -> None:  # noqa: N802 - the stdlib's spelling
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))

        if self.path.endswith("/tokenize"):
            # Not appended to RECEIVED: calibration is not offered traffic, and tests
            # that count requests would see it as one.
            if type(self).no_tokenizer:
                self.send_response(404)
                self.end_headers()
                return
            count = int(len(str(body["prompt"]).split()) * type(self).ratio)
            payload = json.dumps({"count": count}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        RECEIVED.append(body)

        if type(self).refuse:
            self.send_response(503)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chat = self.path.endswith("/chat/completions")
        if chat:
            # A real chat stream opens with a role-only delta carrying no text. TTFT
            # has to come from the first chunk with content, not from this one.
            self.wfile.write(
                f"data: {json.dumps({'choices': [{'delta': {'role': 'assistant'}}]})}\n\n".encode()
            )
        for _ in range(body["max_tokens"]):
            chunk = (
                {"choices": [{"delta": {"content": "x"}}]} if chat else {"choices": [{"text": "x"}]}
            )
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        prompt_tokens = len(prompt_of(body).split())
        usage = {"choices": [], "usage": {"prompt_tokens": prompt_tokens}}
        self.wfile.write(f"data: {json.dumps(usage)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_: Any) -> None:
        """Silence. The handler's default writes to stderr on every request."""


@pytest.fixture
def engine():
    RECEIVED.clear()
    FakeEngine.refuse = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEngine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_it_serves_and_records(engine: str, tmp_path: Path) -> None:
    out = tmp_path / "load.jsonl"
    gen = Load(engine, out, model="lab", rps=50, prompt_tokens=64, output_tokens=4).run(0.3)

    assert gen.sent > 0
    assert gen.ok == gen.sent
    assert gen.failed == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(records) == gen.sent
    assert all(r["status"] == 200 for r in records)


def test_every_prompt_is_unique(engine: str, tmp_path: Path) -> None:
    """Prefix caching is on in the shipped config. Repeating a prompt makes the
    second request nearly free and leaves no KV footprint, so a generator that sends
    one prompt over and over reports a cache that never fills at any rate."""
    Load(engine, None, model="lab", rps=80, prompt_tokens=64, output_tokens=1).run(0.3)

    prompts = [prompt_of(r) for r in RECEIVED]
    assert len(prompts) > 3
    assert len(set(prompts)) == len(prompts)
    # Not merely different somewhere — different from the first token, or the shared
    # head is still a cache hit.
    assert len({p[:40] for p in prompts}) == len(prompts)


def test_it_talks_to_the_chat_route_by_default(engine: str, tmp_path: Path) -> None:
    """vLLM 0.31 serving Qwen2.5-7B-Instruct answers /v1/chat/completions and returns
    404 for /v1/completions. Defaulting to the legacy route recorded three minutes of
    404s at exactly the requested arrival rate — a load test that looks like it ran."""
    gen = Load(engine, None, model="lab", rps=40, prompt_tokens=32, output_tokens=2)
    assert gen.endpoint == "chat"
    gen.run(0.2)
    assert gen.ok == gen.sent
    assert all("messages" in r for r in RECEIVED)


def test_the_legacy_route_still_works_when_asked_for(engine: str, tmp_path: Path) -> None:
    gen = Load(
        engine,
        None,
        model="lab",
        rps=40,
        prompt_tokens=32,
        output_tokens=2,
        endpoint="completions",
    ).run(0.2)
    assert gen.ok == gen.sent
    assert all("prompt" in r for r in RECEIVED)


def test_a_run_that_serves_nothing_gives_up(tmp_path: Path) -> None:
    """A wrong endpoint or model used to burn the whole window recording the same
    error. It stops once ABORT_AFTER requests have failed with nothing served."""
    from admitperf.load import ABORT_AFTER

    gen = Load("http://127.0.0.1:1", None, model="lab", rps=500, output_tokens=1).run(30)

    assert gen.aborted is not None
    assert "none succeeded" in gen.aborted
    # Gave up early rather than offering 500/s for thirty seconds.
    assert gen.sent < ABORT_AFTER * 20


def test_ttft_skips_the_role_only_opening_chunk(engine: str, tmp_path: Path) -> None:
    """A chat stream's first chunk announces the role and carries no text. Timing it
    would report a TTFT that no user experiences."""
    out = tmp_path / "load.jsonl"
    Load(engine, out, model="lab", rps=20, prompt_tokens=32, output_tokens=5).run(0.3)

    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records
    # Five content chunks, and the role chunk is not one of them.
    assert all(r["output_tokens"] == 5 for r in records)


def test_it_asks_the_engine_not_to_stop_early(engine: str, tmp_path: Path) -> None:
    """Without ignore_eos the model stops at its own end token, decode collapses, and
    each request holds its cache for a fraction of the intended time."""
    Load(engine, None, model="lab", rps=50, prompt_tokens=32, output_tokens=2).run(0.2)

    assert RECEIVED
    assert all(r["ignore_eos"] is True for r in RECEIVED)
    assert all(r["stream"] is True for r in RECEIVED)


def test_ttft_is_measured_separately_from_total(engine: str, tmp_path: Path) -> None:
    """TTFT is what admission control protects, and it is unobservable without
    streaming — an unstreamed response arrives once, so the only measurable number
    mixes queueing with generation length."""
    out = tmp_path / "load.jsonl"
    Load(engine, out, model="lab", rps=20, prompt_tokens=32, output_tokens=40).run(0.3)

    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert records
    for r in records:
        assert r["ttft_ms"] is not None
        assert r["ttft_ms"] <= r["latency_ms"]


def test_arrivals_do_not_wait_for_completions(engine: str, tmp_path: Path) -> None:
    """The open-loop property. A closed loop holding N in flight slows down exactly
    as much as the server does, the queue never grows, and there is nothing for a
    policy to shed. Served at 400 tokens each, these responses cannot all finish
    inside the window, so a closed loop would send far fewer."""
    gen = Load(engine, None, model="lab", rps=60, prompt_tokens=32, output_tokens=400).run(0.5)

    # 60 rps for 0.5s offers ~30. A closed loop at any small concurrency would be
    # bounded by completions instead.
    assert gen.sent >= 10


def test_a_refusal_is_an_outcome_not_a_failure(engine: str, tmp_path: Path) -> None:
    """Admission control produces 503s on purpose. Counting them as errors would
    make a working policy indistinguishable from a broken generator."""
    FakeEngine.refuse = True
    out = tmp_path / "load.jsonl"
    gen = Load(engine, out, model="lab", rps=60, prompt_tokens=32, output_tokens=1).run(0.3)

    assert gen.refused == gen.sent
    assert gen.failed == 0
    assert gen.ok == 0
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(r["status"] == 503 and r["refused"] for r in records)


def test_the_engines_own_prompt_count_is_recorded(engine: str, tmp_path: Path) -> None:
    """The prompt is built from a words-to-tokens estimate, so whether a KV policy
    can fire depends on what the engine counted, not on what was asked for."""
    out = tmp_path / "load.jsonl"
    gen = Load(engine, out, model="lab", rps=40, prompt_tokens=130, output_tokens=1).run(0.3)

    assert gen.prompt_tokens_seen
    # The fake counts words; the real one counts tokens. Either way it is reported.
    assert all(n > 0 for n in gen.prompt_tokens_seen)


def test_a_dead_engine_is_recorded_as_an_error(tmp_path: Path) -> None:
    """Nothing raises out of a run. An unreachable engine must leave a trace saying
    so rather than an empty file that reads as a quiet cluster."""
    out = tmp_path / "load.jsonl"
    # Port 1 is reserved and nothing listens there.
    gen = Load("http://127.0.0.1:1", out, model="lab", rps=40, output_tokens=1).run(0.2)

    assert gen.sent > 0
    assert gen.ok == 0
    assert gen.failed == gen.sent
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert all("error" in r for r in records)
