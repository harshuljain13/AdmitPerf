#!/usr/bin/env python3
"""A pretend vLLM, so the whole CLI can be exercised without a GPU.

    python scripts/fake_vllm.py                     # serves on :8000
    admitperf smoke --engine-url http://127.0.0.1:8000
    admitperf run --policy no_admission --engine-url http://127.0.0.1:8000

It speaks the three endpoints AdmitPerf touches — /v1/models, /metrics and a
streaming /v1/chat/completions — and, importantly, it gets *busy*. KV usage
rises with the number of requests in flight and tokens slow down under load, so
a threshold policy actually has something to react to. A fake that always
reports an idle fleet would make every policy look identical.

Stdlib only, no dependencies. This is a test fixture, not a simulator: none of
its numbers mean anything about real hardware.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"running": 0, "waiting": 0, "completed": 0, "preemptions": 0}

# Cumulative timing counters, the same sum/count pairs vLLM publishes. A
# predictive policy derives its service rates from these, so a fake without
# them leaves such a policy permanently uncalibrated — which looks exactly like
# a policy that decided everything was admissible.
TIMING = {
    "prefill_s": 0.0,
    "prefill_tokens": 0.0,
    "decode_s": 0.0,
    "itl_s": 0.0,
    "itl_n": 0.0,
    "queue_s": 0.0,
}
LOCK = threading.Lock()

# Concurrency at which the pretend engine is considered saturated. Past this,
# KV pressure approaches 1.0 and tokens slow down.
CAPACITY = 8


def kv_fraction() -> float:
    with LOCK:
        load = STATE["running"] + STATE["waiting"]
    return min(0.99, round(load / CAPACITY, 3))


METRICS_TEMPLATE = """# HELP vllm:num_requests_running Number of requests currently running on GPU.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{{model_name="lab"}} {running}.0
# HELP vllm:num_requests_waiting Number of requests waiting to be processed.
# TYPE vllm:num_requests_waiting gauge
vllm:num_requests_waiting{{model_name="lab"}} {waiting}.0
# HELP vllm:kv_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{{model_name="lab"}} {kv}
# HELP vllm:num_preemptions_total Cumulative number of preemptions.
# TYPE vllm:num_preemptions_total counter
vllm:num_preemptions_total{{model_name="lab"}} {preemptions}.0
# HELP vllm:request_queue_time_seconds Time spent in WAITING phase.
# TYPE vllm:request_queue_time_seconds histogram
vllm:request_queue_time_seconds_sum{{model_name="lab"}} {queue_s}
vllm:request_queue_time_seconds_count{{model_name="lab"}} {completed}.0
# HELP vllm:request_prefill_time_seconds Prefill duration.
# TYPE vllm:request_prefill_time_seconds histogram
vllm:request_prefill_time_seconds_sum{{model_name="lab"}} {prefill_s}
vllm:request_prefill_time_seconds_count{{model_name="lab"}} {completed}.0
# HELP vllm:request_prefill_kv_computed_tokens Prompt tokens prefilled.
# TYPE vllm:request_prefill_kv_computed_tokens histogram
vllm:request_prefill_kv_computed_tokens_sum{{model_name="lab"}} {prefill_tokens}
vllm:request_prefill_kv_computed_tokens_count{{model_name="lab"}} {completed}.0
# HELP vllm:request_decode_time_seconds Decode duration.
# TYPE vllm:request_decode_time_seconds histogram
vllm:request_decode_time_seconds_sum{{model_name="lab"}} {decode_s}
vllm:request_decode_time_seconds_count{{model_name="lab"}} {completed}.0
# HELP vllm:inter_token_latency_seconds Time between output tokens.
# TYPE vllm:inter_token_latency_seconds histogram
vllm:inter_token_latency_seconds_sum{{model_name="lab"}} {itl_s}
vllm:inter_token_latency_seconds_count{{model_name="lab"}} {itl_n}
# HELP vllm:prefix_cache_queries_total Prefix cache queries.
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{{model_name="lab"}} {completed}.0
vllm:prefix_cache_hits_total{{model_name="lab"}} 0.0
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # keep the console readable
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            payload = json.dumps({"object": "list", "data": [{"id": "lab"}]}).encode()
            self._send(200, payload, "application/json")
        elif self.path == "/metrics":
            with LOCK:
                snapshot = dict(STATE)
                timing = dict(TIMING)
            body = METRICS_TEMPLATE.format(
                running=snapshot["running"],
                waiting=snapshot["waiting"],
                kv=kv_fraction(),
                preemptions=snapshot["preemptions"],
                completed=snapshot["completed"],
                **{k: round(v, 4) for k, v in timing.items()},
            ).encode()
            self._send(200, body, "text/plain; version=0.0.4")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._send(404, b"not found", "text/plain")
            return

        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        n_tokens = min(int(request.get("max_tokens", 32)), 64)

        with LOCK:
            over = max(0, STATE["running"] - CAPACITY)
            STATE["waiting"] += 1 if over else 0
            STATE["running"] += 1
        try:
            self._stream(n_tokens)
        finally:
            with LOCK:
                STATE["running"] -= 1
                STATE["waiting"] = max(0, STATE["waiting"] - 1)
                STATE["completed"] += 1

    def _stream(self, n_tokens: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Busier fleet, slower tokens — so TTFT and inter-token gaps actually
        # degrade under load and a policy's effect is visible in the numbers.
        with LOCK:
            load = STATE["running"]
        slowdown = 1.0 + max(0.0, (load - CAPACITY) / CAPACITY)

        prefill_s = 0.05 * slowdown
        prefill_tokens = 200 * slowdown
        time.sleep(prefill_s)

        decode_started = time.monotonic()
        for i in range(n_tokens):
            chunk = {
                "id": "fake",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": f"tok{i} "}}],
            }
            self._chunk(f"data: {json.dumps(chunk)}\n\n")
            time.sleep(0.01 * slowdown * random.uniform(0.8, 1.2))

        decode_s = time.monotonic() - decode_started
        with LOCK:
            TIMING["prefill_s"] += prefill_s
            TIMING["prefill_tokens"] += prefill_tokens
            TIMING["decode_s"] += decode_s
            TIMING["itl_s"] += decode_s
            TIMING["itl_n"] += max(1, n_tokens - 1)
            TIMING["queue_s"] += 0.05 * max(0.0, slowdown - 1.0)

        self._chunk("data: [DONE]\n\n")
        self._chunk("")  # terminating chunk

    def _chunk(self, text: str) -> None:
        payload = text.encode()
        self.wfile.write(f"{len(payload):X}\r\n".encode())
        self.wfile.write(payload + b"\r\n")
        self.wfile.flush()


def main() -> None:
    global CAPACITY

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--capacity", type=int, default=CAPACITY)
    args = parser.parse_args()
    CAPACITY = args.capacity

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"fake vLLM on http://127.0.0.1:{args.port}  (capacity {CAPACITY})")
    print(f"  admitperf infra smoke --engine-url http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
