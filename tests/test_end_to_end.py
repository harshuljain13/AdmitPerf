"""The whole stack against a fake vLLM.

Workload → policy → runner → engine adapter → HTTP → results bundle, with only
the server faked. This is the test that would have caught the fact that nothing
was wired together.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from admitperf.bench.results import percentiles, summarize, write_bundle
from admitperf.bench.workloads.poisson import PoissonWorkload
from admitperf.core.registry import get_policy
from admitperf.core.runner import Runner, RunnerConfig
from admitperf.engines.vllm import VllmConfig, VllmEngine

METRICS = """
vllm:num_requests_running{{model_name="lab"}} {running}
vllm:num_requests_waiting{{model_name="lab"}} {waiting}
vllm:kv_cache_usage_perc{{model_name="lab"}} {kv}
vllm:num_preemptions_total{{model_name="lab"}} 0.0
vllm:request_queue_time_seconds_sum{{model_name="lab"}} 1.0
vllm:request_queue_time_seconds_count{{model_name="lab"}} 10.0
vllm:prefix_cache_queries_total{{model_name="lab"}} 5.0
vllm:prefix_cache_hits_total{{model_name="lab"}} 4.0
"""


def fake_vllm(kv: float):
    """A server that reports a fixed KV pressure and streams three tokens."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/metrics":
            return httpx.Response(200, text=METRICS.format(running=2, waiting=1, kv=kv))
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "lab"}]})
        body = "\n".join(
            "data: " + json.dumps({"choices": [{"delta": {"content": tok}}]})
            for tok in ("a", "b", "c")
        )
        return httpx.Response(200, text=body + "\ndata: [DONE]\n")

    return handler


def _engine(kv: float) -> VllmEngine:
    return VllmEngine(
        VllmConfig(base_url="http://fake"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake_vllm(kv))),
    )


async def _run(policy: str, kv: float, n: int = 12) -> tuple:
    engine = _engine(kv)
    runner = Runner(
        workload=PoissonWorkload(n_requests=n, rate_per_s=500.0, seed=1),
        policy=get_policy(policy),
        engine=engine,
        config=RunnerConfig(scrape_interval_s=0.01),
    )
    try:
        result = await runner.run()
    finally:
        await engine.aclose()
    return result, summarize(result)


async def test_baseline_admits_everything_and_measures_it() -> None:
    result, summary = await _run("no_admission", kv=0.1)

    assert summary["offered"] == 12
    assert summary["admitted"] == 12
    assert summary["rejected"] == 0
    assert summary["completed"] == 12
    # Timings are measured client-side, so they exist even though the fake
    # server publishes no per-request latency at all.
    assert summary["ttft_ms"]["p50"] is not None
    assert summary["sources"]["ttft_ms"] == "client"


async def test_policy_rejects_under_pressure_and_the_baseline_does_not() -> None:
    """The comparison the whole project exists to make, in miniature."""
    _, baseline = await _run("no_admission", kv=0.95)
    _, threshold = await _run("kv_threshold", kv=0.95)

    assert baseline["rejected"] == 0
    assert threshold["rejected"] == 12
    assert threshold["reject_reasons"] == {"kv_pressure": 12}
    assert threshold["admit_rate"] == 0.0


async def test_policy_admits_when_there_is_headroom() -> None:
    _, summary = await _run("kv_threshold", kv=0.10)
    assert summary["admitted"] == 12
    assert summary["rejected"] == 0


async def test_engine_state_reaches_the_decision_record() -> None:
    result, _ = await _run("no_admission", kv=0.73)
    assert all(d.kv_used_fraction == 0.73 for d in result.decisions)
    assert all(d.waiting_requests == 1 for d in result.decisions)


async def test_bundle_is_written_and_readable(tmp_path: Path) -> None:
    result, _ = await _run("kv_threshold", kv=0.95)
    out = write_bundle(
        tmp_path / "run", result=result, manifest={"policy": "kv_threshold", "engine": "vllm"}
    )

    summary = json.loads((out / "summary.json").read_text())
    assert summary["rejected"] == 12
    # Unmeasurable metrics are named, with a reason, rather than estimated.
    assert "preemption_loss_bytes" in summary["unavailable"]

    decisions = [json.loads(line) for line in (out / "decisions.jsonl").read_text().splitlines()]
    assert len(decisions) == 12
    assert decisions[0]["http_status"] == 429
    assert (out / "manifest.json").exists()
    assert (out / "outcomes.jsonl").exists()


def test_percentiles_on_a_known_distribution() -> None:
    p = percentiles([float(i) for i in range(1, 101)])
    assert p["p50"] == 50.0
    assert p["p95"] == 95.0
    assert p["p99"] == 99.0


def test_percentiles_of_nothing_are_none_not_zero() -> None:
    """Zero would read as an impossibly fast run."""
    assert percentiles([]) == {"p50": None, "p95": None, "p99": None}
