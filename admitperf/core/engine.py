"""vLLM adapter — scrape its telemetry, run requests against it, time them.

Metric names here are not guesses. They were read off a working two-replica
gateway (llm-inference-experiments/module7-admission-and-routing), which is
also why `kv_cache_usage_perc` appears rather than the `gpu_cache_usage_perc`
older docs mention: vLLM v1 renamed it.

Two sources of truth, deliberately kept apart:

  /metrics   → what the fleet looks like now. Drives decisions.
  the stream → what happened to one request. Drives the scoreboard.

They are not interchangeable. vLLM publishes TTFT as a sum/count pair, which
is a running mean over every request the server has ever handled. You cannot
get a p95 from it, and you cannot attribute it to the request whose admission
you are evaluating. So per-request timings are taken on this side of the wire.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from admitperf.core.api import Request, SystemState
from admitperf.core.ports import RequestOutcome

#: Signals this adapter can fill in. Checked against a policy's `requires` at
#: startup, so a policy that needs something vLLM does not expose fails loudly.
VLLM_CAPABILITIES = frozenset(
    {
        "kv_used_fraction",
        "running_requests",
        "waiting_requests",
        "per_tenant_running",
    }
)

#: Metric names AdmitPerf reads. Kept as a mapping rather than scattered string
#: literals so a vLLM rename shows up in one place.
VLLM_METRICS = {
    "running": "vllm:num_requests_running",
    "waiting": "vllm:num_requests_waiting",
    "kv_usage": "vllm:kv_cache_usage_perc",
    "preemptions": "vllm:num_preemptions_total",
    "queue_time_sum": "vllm:request_queue_time_seconds_sum",
    "queue_time_count": "vllm:request_queue_time_seconds_count",
    "prefix_queries": "vllm:prefix_cache_queries_total",
    "prefix_hits": "vllm:prefix_cache_hits_total",
}

_PROM_LINE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)\s*$"
)


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse an exposition payload into name → value.

    Labels are dropped: with one replica per adapter there is nothing to
    disambiguate, and summing across label sets is what the fleet view does.
    """
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _PROM_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        value = float(m.group("value"))
        out[name] = out.get(name, 0.0) + value if name in out else value
    return out


class KVScaleError(RuntimeError):
    """kv_cache_usage_perc was outside both plausible scales."""


@dataclass
class VllmConfig:
    base_url: str = "http://127.0.0.1:8000"
    model: str = "lab"
    request_timeout_s: float = 120.0
    #: 2s is fine against localhost and far too tight against a remote HTTPS
    #: endpoint under load. A timing-out scrape leaves the policy reading a
    #: stale, idle-looking fleet, which is worse than no policy at all.
    scrape_timeout_s: float = 10.0


class VllmEngine:
    """Drives one vLLM replica over its OpenAI-compatible API."""

    name = "vllm"

    def __init__(self, config: VllmConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=config.request_timeout_s)
        self._owns_client = client is None
        self._kv_scale: float | None = None

    def capabilities(self) -> frozenset[str]:
        return VLLM_CAPABILITIES

    # --- telemetry --------------------------------------------------------

    async def fetch_state(self) -> SystemState:
        resp = await self._client.get(
            f"{self.config.base_url}/metrics", timeout=self.config.scrape_timeout_s
        )
        resp.raise_for_status()
        return self.state_from_metrics(parse_prometheus(resp.text))

    def state_from_metrics(self, metrics: dict[str, float]) -> SystemState:
        return SystemState(
            now=time.monotonic(),
            kv_used_fraction=self._kv_fraction(metrics),
            running_requests=int(metrics.get(VLLM_METRICS["running"], 0)),
            waiting_requests=int(metrics.get(VLLM_METRICS["waiting"], 0)),
            running_agents=0,
            per_tenant_running={},
            per_tenant_admitted_recent={},
            engine_metrics=metrics,
        )

    def _kv_fraction(self, metrics: dict[str, float]) -> float | None:
        """Normalise KV usage to 0–1, deciding the scale exactly once.

        Some vLLM builds report this as a fraction, others as a percentage.
        The obvious guess — treat anything above 1 as a percentage — silently
        misreads a genuinely full cache, which is the one moment an admission
        policy most needs the truth. So the scale is inferred once and then
        held: a value that later contradicts it is an error, not a nudge.
        """
        raw = metrics.get(VLLM_METRICS["kv_usage"])
        if raw is None:
            return None

        if self._kv_scale is None:
            if 0.0 <= raw <= 1.0:
                # Ambiguous on its own (0.5 could be 0.5% or 50%), so assume
                # fraction and let a later >1 reading correct us upward.
                self._kv_scale = 1.0
            elif raw <= 100.0:
                self._kv_scale = 100.0
            else:
                raise KVScaleError(f"{VLLM_METRICS['kv_usage']} = {raw}, outside 0-100")
        elif self._kv_scale == 1.0 and raw > 1.0:
            self._kv_scale = 100.0

        if raw > self._kv_scale:
            raise KVScaleError(
                f"{VLLM_METRICS['kv_usage']} = {raw} exceeds the {self._kv_scale:g} "
                "scale established earlier; the series cannot be trusted"
            )
        return raw / self._kv_scale

    @property
    def kv_scale(self) -> float | None:
        """Recorded in the run manifest so a reader knows how KV was read."""
        return self._kv_scale

    # --- running work -----------------------------------------------------

    async def submit(self, req: Request) -> RequestOutcome:
        """Stream one completion, timing first token and every gap after it."""
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": self._prompt_for(req)}],
            "max_tokens": req.expected_output_tokens or 128,
            "stream": True,
            "temperature": 0.0,
        }

        started = time.monotonic()
        first_token_at: float | None = None
        gaps: list[float] = []
        previous = started
        tokens = 0

        try:
            async with self._client.stream(
                "POST", f"{self.config.base_url}/v1/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    body = line[6:].strip()
                    if body == "[DONE]":
                        break
                    if not self._has_content(body):
                        continue

                    at = time.monotonic()
                    if first_token_at is None:
                        first_token_at = at
                    else:
                        gaps.append((at - previous) * 1000.0)
                    previous = at
                    tokens += 1
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            return RequestOutcome(
                request_id=req.request_id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                total_ms=(time.monotonic() - started) * 1000.0,
            )

        ttft_ms = (first_token_at - started) * 1000.0 if first_token_at else None
        return RequestOutcome(
            request_id=req.request_id,
            status="completed",
            ttft_ms=ttft_ms,
            tbt_ms=tuple(gaps),
            total_ms=(time.monotonic() - started) * 1000.0,
            output_tokens=tokens,
            met_deadline=self._met_deadline(req, ttft_ms),
        )

    @staticmethod
    def _has_content(body: str) -> bool:
        chunk = json.loads(body)
        return any(c.get("delta", {}).get("content") for c in chunk.get("choices", ()))

    @staticmethod
    def _met_deadline(req: Request, ttft_ms: float | None) -> bool | None:
        if req.deadline_ttft_ms is None or ttft_ms is None:
            return None
        return ttft_ms <= req.deadline_ttft_ms

    def _prompt_for(self, req: Request) -> str:
        """Synthesise a prompt of roughly the requested size.

        Token count is what matters for KV pressure, not the words. Roughly
        four characters per token is close enough for load generation, and the
        request id keeps prompts distinct so prefix caching does not silently
        serve every request from cache and flatten the experiment.
        """
        target_chars = max(16, req.input_tokens * 4)
        seed = f"[{req.request_id}] "
        filler = "the quick brown fox jumps over the lazy dog. "
        return seed + (filler * (target_chars // len(filler) + 1))[: target_chars - len(seed)]

    async def health(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self.config.base_url}/v1/models", timeout=self.config.scrape_timeout_s
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "VLLM_CAPABILITIES",
    "VLLM_METRICS",
    "KVScaleError",
    "VllmConfig",
    "VllmEngine",
    "parse_prometheus",
]
