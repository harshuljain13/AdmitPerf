"""The signals AdmitPerf ships.

Data, not a class. Each one is a promise that two deployments reporting the same
number mean the same thing, so adding one is a deliberate act — and a promise
nobody checks is worse than no entry.

A host whose metrics are named differently does not edit this file:

    from admitperf.core.signals import KV_PRESSURE
    KV_PRESSURE.add_source("acme.cache.used_frac")
"""

from __future__ import annotations

from collections.abc import Mapping

from admitperf.core.signal import Signal


def _dcgm_gpu_util(m: Mapping[str, float]) -> float:
    """DCGM reports 0-100; this signal is a fraction. A host should not have to know
    that 94.0 means 0.94 here, and every host getting it wrong differently is how a
    shared name stops meaning anything."""
    return m["DCGM_FI_DEV_GPU_UTIL"] / 100.0


def _vllm_prefix_hit_rate(m: Mapping[str, float]) -> float:
    """Two counters, not a gauge. Cumulative since start, so a host wanting a
    windowed rate adds its own source."""
    return m["vllm:prefix_cache_hits_total"] / m["vllm:prefix_cache_queries_total"]


KV_PRESSURE = Signal(
    "kv_pressure",
    # vLLM renamed gpu_cache_usage_perc -> kv_cache_usage_perc. Listing both makes a
    # version difference a non-event instead of a portability failure.
    ["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"],
    lo=0.0,
    hi=1.0,
    help="Share of the KV cache in use. The signal most policies watch, and the one "
    "that silently stays flat on a small model or short requests.",
)

QUEUE_DEPTH = Signal(
    "queue_depth",
    ["vllm:num_requests_waiting"],
    lo=0.0,
    help="Requests accepted by the engine but not yet running.",
)

RUNNING = Signal(
    "running",
    ["vllm:num_requests_running"],
    lo=0.0,
    help="Requests executing now. Against a known concurrency cap this distinguishes "
    "a full batch from a backed-up queue.",
)

GPU_UTIL = Signal(
    "gpu_util",
    [_dcgm_gpu_util],
    lo=0.0,
    hi=1.0,
    help="GPU busy fraction. No surveyed admission policy reads hardware telemetry; "
    "it tells compute-bound from memory-bound, which decides whether a KV-pressure "
    "policy is the right instrument at all.",
)

PREFIX_HIT_RATE = Signal(
    "prefix_hit_rate",
    [_vllm_prefix_hit_rate],
    lo=0.0,
    hi=1.0,
    help="Share of prompt tokens served from cache. Pairs with kv_pressure: high use "
    "with high reuse is healthy, high use with low reuse is congestion.",
)

#: Every shipped signal. The log records all of them per decision, not only the ones
#: a policy read — which is what lets a report say "your signal never moved but queue
#: depth hit 61", and what makes replaying another policy over the same log possible.
ALL: tuple[Signal, ...] = (
    KV_PRESSURE,
    QUEUE_DEPTH,
    RUNNING,
    GPU_UTIL,
    PREFIX_HIT_RATE,
)
