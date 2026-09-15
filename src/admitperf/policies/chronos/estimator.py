"""Turn the engine's cumulative counters into current service rates.

Any policy that predicts a response time needs to know how fast the fleet
serves work. This module answers that from the engine's own telemetry, so the
numbers are measured rather than configured — and it is deliberately separate
from any policy, because QLM, Fluid-WAIT and anything else deadline-aware will
need the same thing.

The subtlety is that vLLM publishes `_sum`/`_count` pairs, which are
*cumulative since the server started*. Dividing them gives a lifetime mean,
dominated by whatever happened first — typically a cold, uncontended period
that flatters the fleet. Under load that stale mean would make a predictive
policy optimistic exactly when it needs to be careful.

So rates come from the **delta** between consecutive scrapes: the work
completed in the last window only.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass

from admitperf.core.api import SystemState
from admitperf.policies.chronos.wcrt import CostModel

#: Counter pairs, named once so a vLLM rename shows up in one place.
PREFILL_TIME = ("vllm:request_prefill_time_seconds_sum", "vllm:request_prefill_time_seconds_count")
PREFILL_TOKENS = (
    "vllm:request_prefill_kv_computed_tokens_sum",
    "vllm:request_prefill_kv_computed_tokens_count",
)
INTER_TOKEN = ("vllm:inter_token_latency_seconds_sum", "vllm:inter_token_latency_seconds_count")
DECODE_TIME = ("vllm:request_decode_time_seconds_sum", "vllm:request_decode_time_seconds_count")
QUEUE_TIME = ("vllm:request_queue_time_seconds_sum", "vllm:request_queue_time_seconds_count")


@dataclass(frozen=True)
class ServiceRates:
    """How fast the fleet is currently serving work.

    Every field is optional because a fleet that has served nothing yet cannot
    be characterised. A policy must handle that rather than assume a default:
    guessing a rate is how a predictive policy silently becomes a coin flip.
    """

    prefill_tokens_per_s: float | None = None
    seconds_per_output_token: float | None = None
    mean_decode_seconds: float | None = None
    mean_queue_seconds: float | None = None
    #: Requests that completed in the window these rates describe.
    samples: int = 0

    @property
    def is_calibrated(self) -> bool:
        """Enough signal to predict a response time."""
        return (
            self.prefill_tokens_per_s is not None
            and self.prefill_tokens_per_s > 0
            and self.seconds_per_output_token is not None
            and self.seconds_per_output_token > 0
        )


def _delta(
    now: dict[str, float], before: dict[str, float], pair: tuple[str, str]
) -> tuple[float, float]:
    """Work completed between two scrapes.

    Counters only ever increase, so a negative delta means the engine
    restarted. Treating that as zero rather than a negative rate keeps one
    restart from poisoning every later estimate.
    """
    sum_key, count_key = pair
    d_sum = now.get(sum_key, 0.0) - before.get(sum_key, 0.0)
    d_count = now.get(count_key, 0.0) - before.get(count_key, 0.0)
    if d_sum < 0 or d_count < 0:
        return 0.0, 0.0
    return d_sum, d_count


class ServiceRateEstimator:
    """Tracks scrape-to-scrape deltas and reports current rates."""

    def __init__(self, *, min_samples: int = 1) -> None:
        if min_samples < 1:
            raise ValueError(f"min_samples must be >= 1, got {min_samples}")
        self.min_samples = min_samples
        self._previous: dict[str, float] | None = None
        #: Last rates that met the sample threshold. Held because most scrapes
        #: land between completions: with a 100ms interval and requests taking
        #: seconds, most windows are empty, and reporting "uncalibrated" then
        #: would make the policy flicker in and out of working.
        self._last_good = ServiceRates()

    def update(self, state: SystemState) -> ServiceRates:
        metrics = state.engine_metrics
        if self._previous is None:
            self._previous = dict(metrics)
            # First scrape has no window to difference against. Fall back to
            # the lifetime mean so the policy is usable from the second
            # decision rather than the second completed request.
            self._last_good = self._lifetime(metrics)
            return self._last_good

        rates = self._windowed(metrics, self._previous)
        if rates.samples >= self.min_samples:
            self._previous = dict(metrics)
            self._last_good = rates
        return self._last_good

    # --- internals --------------------------------------------------------

    def _windowed(self, now: dict[str, float], before: dict[str, float]) -> ServiceRates:
        pt_sum, _ = _delta(now, before, PREFILL_TIME)
        tok_sum, tok_count = _delta(now, before, PREFILL_TOKENS)
        itl_sum, itl_count = _delta(now, before, INTER_TOKEN)
        dec_sum, dec_count = _delta(now, before, DECODE_TIME)
        q_sum, q_count = _delta(now, before, QUEUE_TIME)

        return ServiceRates(
            prefill_tokens_per_s=_ratio(tok_sum, pt_sum),
            seconds_per_output_token=_ratio(itl_sum, itl_count),
            mean_decode_seconds=_ratio(dec_sum, dec_count),
            mean_queue_seconds=_ratio(q_sum, q_count),
            samples=int(tok_count),
        )

    def _lifetime(self, metrics: dict[str, float]) -> ServiceRates:
        """Cumulative means. Coarse, but better than nothing on the first read."""
        return ServiceRates(
            prefill_tokens_per_s=_ratio(
                metrics.get(PREFILL_TOKENS[0], 0.0), metrics.get(PREFILL_TIME[0], 0.0)
            ),
            seconds_per_output_token=_ratio(
                metrics.get(INTER_TOKEN[0], 0.0), metrics.get(INTER_TOKEN[1], 0.0)
            ),
            mean_decode_seconds=_ratio(
                metrics.get(DECODE_TIME[0], 0.0), metrics.get(DECODE_TIME[1], 0.0)
            ),
            mean_queue_seconds=_ratio(
                metrics.get(QUEUE_TIME[0], 0.0), metrics.get(QUEUE_TIME[1], 0.0)
            ),
            samples=int(metrics.get(PREFILL_TOKENS[1], 0.0)),
        )


def _ratio(numerator: float, denominator: float) -> float | None:
    """A rate, or None when there is nothing to divide by.

    None rather than zero throughout: zero is a claim about the fleet, and a
    policy dividing by an assumed rate produces a confident wrong answer.
    """
    if denominator <= 0 or numerator <= 0:
        return None
    return numerator / denominator


class ArrivalRateWindow:
    """Sliding-window arrival rate, as Algorithm 1 uses for lambda.

    The paper specifies a 60-second window. Reporting the *peak* sub-window
    rate rather than the mean is what makes the utilization test respond to a
    burst instead of averaging it away — a mean over 60s hides exactly the
    overload the test exists to catch.
    """

    def __init__(self, *, window_s: float = 60.0, bucket_s: float = 1.0) -> None:
        if window_s <= 0 or bucket_s <= 0:
            raise ValueError("window and bucket must be positive")
        self.window_s = window_s
        self.bucket_s = bucket_s
        self._buckets: collections.deque[tuple[float, int]] = collections.deque()

    def record(self, now: float | None = None) -> None:
        """Note one arrival."""
        t = time.monotonic() if now is None else now
        slot = int(t / self.bucket_s)
        if self._buckets and self._buckets[-1][0] == slot:
            self._buckets[-1] = (slot, self._buckets[-1][1] + 1)
        else:
            self._buckets.append((slot, 1))
        self._evict(t)

    def _evict(self, now: float) -> None:
        oldest = int((now - self.window_s) / self.bucket_s)
        while self._buckets and self._buckets[0][0] < oldest:
            self._buckets.popleft()

    def mean_rate_hz(self, now: float | None = None) -> float:
        t = time.monotonic() if now is None else now
        self._evict(t)
        if not self._buckets:
            return 0.0
        total = sum(count for _, count in self._buckets)
        span = max(self.bucket_s, (self._buckets[-1][0] - self._buckets[0][0] + 1) * self.bucket_s)
        return total / span

    def peak_rate_hz(self, now: float | None = None) -> float:
        """Busiest bucket in the window — lambda_max."""
        t = time.monotonic() if now is None else now
        self._evict(t)
        if not self._buckets:
            return 0.0
        return max(count for _, count in self._buckets) / self.bucket_s


def fit_cost_model(rates: ServiceRates, *, base: CostModel, chunk_tokens: int) -> CostModel:
    """Replace the paper's roofline alpha/beta with measured values.

    The paper derives them from a roofline model and notes that real
    measurements could take their place. This is that substitution: alpha comes
    from observed prefill throughput, beta from observed inter-token latency.

    gamma is left at the paper's value. Separating a fixed per-iteration
    overhead from the per-token slope needs measurements at two or more batch
    sizes, and a single aggregate window cannot do it.
    """
    alpha = base.alpha_ms_per_token
    if rates.prefill_tokens_per_s:
        alpha = 1000.0 / rates.prefill_tokens_per_s

    beta = base.beta_ms_per_token
    if rates.seconds_per_output_token:
        beta = rates.seconds_per_output_token * 1000.0

    return CostModel(
        alpha_ms_per_token=alpha,
        beta_ms_per_token=beta,
        gamma_ms=base.gamma_ms,
        chunk_tokens=chunk_tokens,
    )


__all__ = ["ArrivalRateWindow", "ServiceRateEstimator", "ServiceRates", "fit_cost_model"]
