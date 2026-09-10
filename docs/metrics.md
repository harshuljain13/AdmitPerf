# Metrics

## Standard (per request)

- **TTFT** — Time To First Token. p50 / p95 / p99.
- **TBT** — Time Between Tokens (inter-token latency). p50 / p95 / p99.
- **Goodput** — completed requests per second that met their SLO.
- **Throughput** — completed requests per second regardless of SLO.
- **GPU utilization** — from engine `/metrics` and (optionally) DCGM.

## Agentic (novel — this is the paper's contribution)

- **Agent-completion rate** — fraction of agent sessions that finished all planned turns within end-to-end budget.
- **Preemption-loss ratio** — sum of KV bytes discarded due to preemption / sum of KV bytes ever generated. Higher = more waste.
- **Jain-fairness-on-agents** — Jain's fairness index applied to per-tenant p95 turn latency. 1.0 = perfect fairness; 1/N = worst.
- **Admission-decision histogram** — fraction of arriving requests that got ADMIT / QUEUE / REJECT. Distribution across decision types.
- **KV pressure over time** — rolling `kv_used_fraction` bucketed at 100ms. Enables the CONCUR-style "middle-phase thrashing" plot.

## Not measured (out of scope)

- Model quality (accuracy, task success beyond completion) — orthogonal.
- Cost — implied by GPU-hour but not first-class.
- Cold-start latency — separate concern from admission.

## Precise definitions (formal)

TODO in Week 1 — needs LaTeX math, will land in the paper's Section 3.
