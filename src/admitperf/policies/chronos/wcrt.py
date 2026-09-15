"""Worst-case response time for a request that has not been admitted yet.

Pure functions: inputs in, numbers out, no engine and no state. That is
deliberate — the arithmetic is the part worth arguing about, and it should be
checkable without a GPU or a mock.

## The idea

Real-time systems admit a task only if its worst-case response time fits inside
its deadline. Applied to LLM serving: before letting a request in, work out how
long it would take in the worst case, and refuse it if that exceeds what the
caller asked for. Refusing early is better than admitting something that will
miss anyway *and* delay everyone queued behind it.

## The model

A request cannot start until a slot frees. Slots do not clear in lockstep —
they free one at a time as individual requests finish — so with `concurrency`
slots each occupied for `service_time`, departures happen at

    departure_rate = concurrency / service_time

and clearing `queued` requests ahead of ours takes

    queue_delay = queued / departure_rate = queued x service_time / concurrency

Once started, our own prefill costs `input_tokens / prefill_tokens_per_s`.

    TTFT_wcrt = queue_delay + own_prefill

Modelling this as whole batches instead — every arrival waiting one full
service time — overestimates badly at shallow queues: a request arriving at a
busy but short-queued fleet was predicted to wait seconds when it would
actually wait for the next single departure.

Inter-token latency is taken directly from measurement: the observed value
already includes whatever contention the batch is under, which is the thing a
predicted value would be trying to approximate.

## What this is not

The published analysis assumes per-task knowledge — arrival periods, execution
bounds, the state of every queued task. An engine's Prometheus endpoint reports
*counts*, not per-request state, so the queue ahead is characterised by its
size and a mean, not individually. That makes this an ingress approximation
with a real bound's shape, not a sound bound. It is named accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass

from admitperf.policies.chronos.estimator import ServiceRates


@dataclass(frozen=True)
class Prediction:
    """What the model expects, and whether it fits the deadline."""

    ttft_ms: float
    queue_delay_ms: float
    own_prefill_ms: float
    tbt_ms: float | None
    #: None when the request carried no deadline to judge against.
    ttft_fits: bool | None = None
    tbt_fits: bool | None = None

    @property
    def fits(self) -> bool:
        """True unless something we can judge is predicted to miss."""
        return self.ttft_fits is not False and self.tbt_fits is not False

    @property
    def violated(self) -> str | None:
        if self.ttft_fits is False:
            return "ttft"
        if self.tbt_fits is False:
            return "tbt"
        return None


def batch_service_seconds(rates: ServiceRates, *, mean_output_tokens: float) -> float:
    """How long one batch of concurrent requests occupies its slots.

    Prefer the engine's measured decode duration; fall back to per-token
    latency times an assumed length only when the former is unavailable.
    """
    if rates.mean_decode_seconds is not None:
        decode = rates.mean_decode_seconds
    elif rates.seconds_per_output_token is not None:
        decode = rates.seconds_per_output_token * mean_output_tokens
    else:
        decode = 0.0

    prefill = 0.0
    if rates.prefill_tokens_per_s:
        # Mean prefill is unknown per-request; the measured decode time already
        # dominates for typical generation lengths, so this stays a small term.
        prefill = mean_output_tokens / rates.prefill_tokens_per_s
    return prefill + decode


def queue_delay_seconds(*, queued: int, concurrency: int, service_seconds: float) -> float:
    """How long until a slot frees for a request `queued` places back.

    Slots free one at a time rather than all at once, so the fleet drains at
    `concurrency / service_seconds` requests per second. Treating it as
    lockstep batches would charge a shallow queue a full service time.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    if queued <= 0:
        return 0.0
    return queued * service_seconds / concurrency


def predict(
    *,
    input_tokens: int,
    expected_output_tokens: int | None,
    running: int,
    waiting: int,
    concurrency: int,
    rates: ServiceRates,
    deadline_ttft_ms: float | None,
    deadline_tbt_ms: float | None,
    mean_output_tokens: float = 128.0,
    safety_factor: float = 1.0,
) -> Prediction:
    """Predict this request's response time and compare it to its deadline.

    `safety_factor` scales the estimate. Above 1.0 the policy is pessimistic
    and sheds earlier; it exists because the mean-based queue model understates
    a heavy tail, and because the cost of admitting a doomed request is borne
    by every request behind it.
    """
    if not rates.is_calibrated:
        raise ValueError("cannot predict without calibrated service rates")

    # mypy: is_calibrated guarantees both are non-None and positive.
    assert rates.prefill_tokens_per_s is not None
    assert rates.seconds_per_output_token is not None

    # Slots already free absorb part of the queue before ours has to wait.
    free_slots = max(0, concurrency - running)
    effective_queue = max(0, waiting + 1 - free_slots)

    service_s = batch_service_seconds(rates, mean_output_tokens=mean_output_tokens)
    queue_delay_ms = (
        queue_delay_seconds(
            queued=effective_queue, concurrency=concurrency, service_seconds=service_s
        )
        * 1000.0
    )

    own_prefill_ms = (input_tokens / rates.prefill_tokens_per_s) * 1000.0
    ttft_ms = (queue_delay_ms + own_prefill_ms) * safety_factor

    tbt_ms = rates.seconds_per_output_token * 1000.0 * safety_factor

    return Prediction(
        ttft_ms=ttft_ms,
        queue_delay_ms=queue_delay_ms,
        own_prefill_ms=own_prefill_ms,
        tbt_ms=tbt_ms,
        ttft_fits=None if deadline_ttft_ms is None else ttft_ms <= deadline_ttft_ms,
        tbt_fits=None if deadline_tbt_ms is None else tbt_ms <= deadline_tbt_ms,
    )


__all__ = ["Prediction", "batch_service_seconds", "predict", "queue_delay_seconds"]
