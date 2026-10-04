"""Watch — the zero-integration on-ramp."""

from __future__ import annotations

import http.server
import threading

import pytest

from admitperf.watch import Watch, parse

METRICS = b"""# HELP vllm:kv_cache_usage_perc KV cache usage
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc 0.94
vllm:num_requests_waiting 41
vllm:num_requests_running{model="x"} 61
DCGM_FI_DEV_GPU_UTIL 96
"""


@pytest.fixture
def endpoint():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(METRICS)

        def log_message(self, *a):
            return None

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/metrics"
    srv.shutdown()


# --- parsing --------------------------------------------------------------


def test_comments_and_type_lines_are_skipped() -> None:
    assert "# HELP" not in parse(METRICS.decode())
    assert parse(METRICS.decode())["vllm:kv_cache_usage_perc"] == 0.94


def test_labels_are_dropped() -> None:
    """Right for a count across replicas; a signal whose metric carries labels and is
    a fraction should be given an explicit source instead of inferred from a sum."""
    assert parse(METRICS.decode())["vllm:num_requests_running"] == 61.0


def test_same_named_series_are_summed() -> None:
    text = 'q{a="1"} 2\nq{a="2"} 3\n'
    assert parse(text)["q"] == 5.0


def test_an_unparseable_line_does_not_sink_the_scrape() -> None:
    """One malformed exporter line must not cost you the whole sample."""
    assert parse("good 1\nthis is not metrics\nalso_good 2\n") == {"good": 1.0, "also_good": 2.0}


# --- watching -------------------------------------------------------------


def test_it_records_metrics_and_the_signals_they_produce(endpoint, tmp_path) -> None:
    out = tmp_path / "t.jsonl"
    w = Watch(endpoint, out, interval=0.01).run(0.1)
    assert w.samples >= 2

    from admitperf.core.log import Log

    rec = Log.read(out)[0]
    assert rec["metrics"]["vllm:kv_cache_usage_perc"] == 0.94
    assert rec["signals"]["kv_pressure"] == 0.94
    assert rec["signals"]["gpu_util"] == pytest.approx(0.96)  # 96 -> 0.96
    assert rec["signals"]["prefix_hit_rate"] is None  # absent, not zero


def test_a_failed_scrape_is_recorded_as_a_failure_not_as_zeros(tmp_path) -> None:
    """Zeros would read as a completely idle cluster, which makes the whole trace
    look like headroom — the most dangerous wrong answer available."""
    out = tmp_path / "t.jsonl"
    w = Watch("http://127.0.0.1:1/metrics", out, interval=0.01).run(0.05)

    from admitperf.core.log import Log

    assert w.samples == 0
    assert w.failures >= 1
    records = Log.read(out)
    assert all("scrape_error" in r for r in records)
    assert not any("signals" in r for r in records)


def test_the_log_is_closed_even_if_watching_is_interrupted(endpoint, tmp_path) -> None:
    """The writer thread is a daemon, so an unclosed log loses its tail."""
    out = tmp_path / "t.jsonl"
    w = Watch(endpoint, out, interval=0.01)
    w.run(0.05)
    assert w.log._closed
