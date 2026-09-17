"""Recording what produced a number.

design.md promises every run pins its engine version and platform. It did not,
and a result that cannot say which vLLM built it cannot be reproduced or
compared against a later one.
"""

from __future__ import annotations

import httpx

from admitperf.bench.environment import (
    _cache_config,
    engine_environment,
    local_environment,
    package_versions,
)

REAL_LINE = (
    'vllm:cache_config_info{block_size="16",cache_dtype="auto",'
    'enable_prefix_caching="False",gpu_memory_utilization="0.9"} 1.0'
)


def test_local_environment_records_the_measuring_machine() -> None:
    """Timings are taken client-side, so the client's platform is part of what
    produced them."""
    env = local_environment()
    assert env["python"] and env["platform"] and env["machine"]
    assert env["packages"]["admitperf"] is not None


def test_a_package_that_is_absent_is_recorded_as_absent() -> None:
    """None rather than a guess: a wrong version hides a mismatch, which is
    worse than an obviously missing one."""
    assert "modal" in package_versions()  # may legitimately be None


def test_cache_config_is_parsed_from_the_metrics_labels() -> None:
    """Worth capturing because it reports what the engine actually resolved,
    which can differ from the flags it was given."""
    parsed = _cache_config(REAL_LINE)
    assert parsed is not None
    assert parsed["block_size"] == "16"
    assert parsed["enable_prefix_caching"] == "False"


def test_cache_config_absent_is_none_not_empty() -> None:
    assert _cache_config("vllm:num_requests_running 1.0") is None


async def test_engine_version_is_captured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.29.0"})
        return httpx.Response(200, text=REAL_LINE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    env = await engine_environment("http://fake", client=client)
    await client.aclose()

    assert env["version"] == "0.29.0"
    assert env["cache_config"]["block_size"] == "16"


async def test_an_engine_without_a_version_endpoint_is_not_fatal() -> None:
    """Older builds and non-vLLM engines have no /version. Absent is the honest
    answer; failing the run would not be."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(404)
        return httpx.Response(200, text=REAL_LINE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    env = await engine_environment("http://fake", client=client)
    await client.aclose()

    assert env["version"] is None
    assert env["cache_config"] is not None


async def test_an_unreachable_engine_does_not_abort_the_run() -> None:
    env = await engine_environment("http://127.0.0.1:9", timeout_s=0.3)
    assert env == {"version": None, "cache_config": None}
