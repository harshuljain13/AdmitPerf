# First results on real hardware

*2026-09-14. Qwen2.5-0.5B-Instruct on one A10G via Modal, vLLM 0.29.*

The first measurements AdmitPerf has produced from a real GPU. Small, and not a
finding about admission control in general — but the mechanism works end to end
and the numbers behave the way the theory says they should.

## Setup

`max_num_seqs=4`, `max_model_len=2048`, prefix caching off, FCFS scheduling.
80 requests at 15 arrivals/second, Poisson, two repeats per policy. Config in
[`../experiments/shedding-vs-tail-latency.yaml`](../experiments/shedding-vs-tail-latency.yaml).

The concurrency cap is the point: four sequences run at a time and the surplus
queues inside vLLM, which is the pressure a policy reads.

## Results

| policy | admit % | TTFT p95 | goodput |
|---|---|---|---|
| `no_admission` | 100.0% | 2285ms ±725 | 0.319 |
| `queue_depth[max_waiting=8]` | 88.1% | 1668ms ±145 | 0.300 |
| `queue_depth[max_waiting=2]` | 63.1% | **951ms ±136** | 0.319 |

Medians across two repeats; ± is half the observed range, not a confidence
interval.

**Shedding 37% of traffic cut tail latency 2.4× at identical goodput.** The
refused requests were ones that would have missed their deadline anyway, so
refusing them cost nothing measurable and bought the admitted ones latency.

The baseline's spread is worth as much as its median: ±725ms against ±136ms for
the strict policy. Unmanaged queueing is not just slower, it is *less
predictable*, and a tail number from a single unmanaged run is close to
meaningless.

## The signal mattered more than the threshold

`kv_cache_usage_perc` never exceeded **0.005** during any run, while the queue
reached **24 deep**. On a 0.5B model with 2k context, the KV cache is far larger
than four short sequences can fill, so the binding constraint is the
concurrency cap, not memory.

A KV-pressure policy in this regime reads a flat line near zero and silently
degrades into admit-everything. That is exactly what happened on the first
attempt: `kv_threshold` admitted 120/120 and was indistinguishable from the
baseline — not because the policy is wrong, but because it was shown nothing.

**Which signal carries the pressure depends on the regime.** KV pressure binds
for long contexts and large batches; queue depth binds when the concurrency cap
is the ceiling. This is the argument for the capability declaration in
[`scope.md`](scope.md): a policy states which signal it needs, and the harness
refuses to run it against an engine that cannot provide one.

It is not yet an argument that queue depth is the *better* signal. Testing that
needs the other regime — a larger model with long contexts, where the cache
genuinely fills.

## What this does not show

- **One model, one GPU, one workload shape.** No claim generalises from this.
- **Two repeats.** Enough to see that the baseline's spread is large; not
  enough to defend a small difference.
- **Synthetic traffic.** Poisson arrivals over three SLO classes, not a captured
  production trace.
- **One published policy ported so far.** `queue_depth` is a threshold, not a
  port of anything. The Chronos-inspired admission test is ported and written
  up in [`reports/chronos-reproduction.md`](../reports/chronos-reproduction.md)
  — which found it far more cautious than the paper reports, and beaten on its
  own terms by a four-line queue-depth rule at the load tested. The rest of the
  roster in [`policies.md`](policies.md) is unbuilt.
- **`goodput` is dominated by the deadlines the workload generator assigns.**
  At these latencies most requests miss the interactive deadline regardless of
  policy, which compresses the differences between policies.

## Reproducing

```bash
admitperf infra up   -c experiments/shedding-vs-tail-latency.yaml
admitperf infra smoke
admitperf bench run  -c experiments/shedding-vs-tail-latency.yaml
admitperf bench compare results/
admitperf infra down
```

Roughly $0.30 of A10G time. Every run bundle records the resolved config, so a
result can be traced to the exact engine settings that produced it.

## Other runs on this hardware

| Run | What it was | Where |
|---|---|---|
| 2026-09-15 | Chronos-inspired vs `no_admission`, same A10G | [`reports/chronos-reproduction.md`](../reports/chronos-reproduction.md) |
| 2026-09-16 | `results/Experiment1` — chronos vs `kv_threshold`. **Not a result**: the workload exceeded `max_model_len`, so a third of every run came back HTTP 400 and was counted as a failure, and the offered load was ~10x what the deployment could serve on time. Both policies scored ~0. `bench run` now refuses to start on the first of those. | `results/Experiment1/`, since renamed `experiments/half-capacity-headroom.yaml` |
