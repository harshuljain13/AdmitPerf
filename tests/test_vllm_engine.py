"""The vLLM adapter.

The contract test is the important one: if vLLM renames a metric, this fails
loudly rather than silently reading zero and turning every KV-aware policy into
an admit-everything baseline.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from admitperf.core.api import Request
from admitperf.core.ports import EngineAdapter
from admitperf.engines.vllm import (
    VLLM_METRICS,
    KVScaleError,
    VllmConfig,
    VllmEngine,
    parse_prometheus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vllm_metrics.txt"


def _engine(handler: object = None) -> VllmEngine:
    if handler is None:
        return VllmEngine(VllmConfig())
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return VllmEngine(VllmConfig(), client=httpx.AsyncClient(transport=transport))


def _req(
    rid: str = "r1", *, inp: int = 100, out: int = 8, deadline: float | None = None
) -> Request:
    return Request(
        request_id=rid,
        tenant_id="t1",
        arrival_time=0.0,
        input_tokens=inp,
        expected_output_tokens=out,
        deadline_ttft_ms=deadline,
    )


def test_satisfies_the_engine_port() -> None:
    assert isinstance(_engine(), EngineAdapter)


# --- the contract test ---------------------------------------------------


def test_every_metric_we_read_exists_in_a_real_payload() -> None:
    """Guards against a vLLM rename. `kv_cache_usage_perc` was
    `gpu_cache_usage_perc` before v1; the next rename should break here."""
    metrics = parse_prometheus(FIXTURE.read_text())
    missing = [name for name in VLLM_METRICS.values() if name not in metrics]
    assert not missing, f"metric names absent from the captured payload: {missing}"


def test_parser_ignores_comments_and_keeps_labelled_values() -> None:
    metrics = parse_prometheus(FIXTURE.read_text())
    assert metrics["vllm:num_requests_running"] == 6.0
    assert metrics["vllm:num_requests_waiting"] == 12.0
    assert not any(k.startswith("#") for k in metrics)


def test_state_is_built_from_the_scrape() -> None:
    engine = _engine()
    state = engine.state_from_metrics(parse_prometheus(FIXTURE.read_text()))

    assert state.running_requests == 6
    assert state.waiting_requests == 12
    assert state.kv_used_fraction == pytest.approx(0.87)
    # Raw metrics ride along, so a policy can reach for something we did not
    # promote to a first-class field.
    assert state.engine_metrics["vllm:num_preemptions_total"] == 3.0


# --- KV scale ------------------------------------------------------------


def test_percentage_scale_is_detected_and_normalised() -> None:
    engine = _engine()
    state = engine.state_from_metrics({VLLM_METRICS["kv_usage"]: 87.0})
    assert state.kv_used_fraction == pytest.approx(0.87)
    assert engine.kv_scale == 100.0


def test_scale_switches_up_once_a_reading_exceeds_one() -> None:
    engine = _engine()
    engine.state_from_metrics({VLLM_METRICS["kv_usage"]: 0.5})
    engine.state_from_metrics({VLLM_METRICS["kv_usage"]: 42.0})
    assert engine.kv_scale == 100.0


def test_a_reading_beyond_the_established_scale_is_an_error() -> None:
    """Better to stop than to quietly emit a KV series that is 100x wrong."""
    engine = _engine()
    engine.state_from_metrics({VLLM_METRICS["kv_usage"]: 55.0})  # fixes scale at 100
    with pytest.raises(KVScaleError, match="cannot be trusted"):
        engine.state_from_metrics({VLLM_METRICS["kv_usage"]: 150.0})


def test_absent_kv_metric_reads_as_unknown_not_empty() -> None:
    assert _engine().state_from_metrics({}).kv_used_fraction is None


# --- request timing ------------------------------------------------------


def _sse(*contents: str) -> str:
    lines = []
    for c in contents:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}))
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


async def test_streaming_response_is_timed_per_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse("Hello", " there", "!"))

    outcome = await _engine(handler).submit(_req(out=3))

    assert outcome.status == "completed"
    assert outcome.output_tokens == 3
    assert outcome.ttft_ms is not None
    # Three tokens means one first-token measurement and two gaps after it.
    assert len(outcome.tbt_ms) == 2


async def test_deadline_verdict_uses_measured_ttft() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse("hi"))

    engine = _engine(handler)
    generous = await engine.submit(_req(deadline=60_000.0))
    impossible = await engine.submit(_req(deadline=0.0))

    assert generous.met_deadline is True
    assert impossible.met_deadline is False


async def test_no_deadline_means_no_verdict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse("hi"))

    assert (await _engine(handler).submit(_req())).met_deadline is None


async def test_engine_error_is_reported_not_raised() -> None:
    """One failed request must not abort a run of thousands."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    outcome = await _engine(handler).submit(_req())
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.total_ms is not None


async def test_health_reflects_the_models_endpoint() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "lab"}]})

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    assert await _engine(ok).health() is True
    assert await _engine(down).health() is False


# --- prompt synthesis ----------------------------------------------------


def test_prompts_are_sized_and_distinct() -> None:
    """Distinct prefixes matter: identical prompts would be served from vLLM's
    prefix cache and the workload would stop exercising prefill at all."""
    engine = _engine()
    a = engine._prompt_for(_req("r1", inp=200))
    b = engine._prompt_for(_req("r2", inp=200))

    assert a != b
    assert len(a) == pytest.approx(800, rel=0.1)
