# Metrics

This document owns every metric definition. Nothing else in the repo should define one; new
metrics arrive here first (see [`../CONTRIBUTING.md`](../CONTRIBUTING.md)).

## Standard (per request)

- **TTFT** — Time To First Token. p50 / p95 / p99.
- **TBT** — Time Between Tokens (inter-token latency). p50 / p95 / p99.
- **Throughput** — completed requests per second, regardless of SLO.
- **Goodput** — completed requests per second that met their SLO.
- **GPU utilization** — from engine `/metrics` and, optionally, DCGM.

## Admission-specific

These are the metrics the standard harnesses (LLMPerf, GenAI-Perf, GuideLLM, MLPerf) do not
report, because none of them treat admission as an experimental axis. They are **not claimed
as novel** — prior art for each is noted below, and
[`prior-art/adversarial_review.md`](prior-art/adversarial_review.md) argues the novelty case
is weak. The contribution is reporting them *together, on one substrate*, not inventing them.

- **Goodput-under-admission** *(primary)* — SLO-meeting admitted requests / total offered
  load. Unlike goodput, the denominator includes everything rejected and deferred, so a
  policy cannot win by refusing most traffic.
- **Admission-decision histogram** — fraction of arrivals that got `ADMIT` / `DEFER` /
  `REJECT`, broken down by `reason`.
- **Defer latency** — wall-clock time a request spent held before its terminal decision.
- **Preemption-loss ratio** — KV bytes discarded to preemption / KV bytes ever generated.
  Higher = more wasted work. *(Described in FastServe, analyzed in Chronos.)*
- **Jain fairness index** — applied to per-tenant p95 turn latency. 1.0 = perfect fairness,
  1/N = worst. *(Jain's index long predates LLM serving.)*
- **KV pressure over time** — rolling `kv_used_fraction` bucketed at 100 ms. Enables the
  CONCUR-style middle-phase thrashing plot.

## Agent-session

- **Agent-completion rate** — fraction of agent sessions that finished all planned turns
  within their end-to-end budget. *(Implicit in MLPerf multi-turn's per-conversation
  success; we make it explicit and report it under admission pressure.)*

Note the scoping from [`prior-art/feasibility_audit.md`](prior-art/feasibility_audit.md):
full-trajectory success scoring requires re-executing task environments (SWE-bench Docker,
τ-bench simulator). v0.1 measures turn completion within budget, not task success.

## Not measured (out of scope)

- Model quality (accuracy, task success beyond completion) — orthogonal.
- Cost — implied by GPU-hour, but not first-class.
- Cold-start latency — a separate concern from admission.

## Results bundle

Every run emits these as JSON plus an environment manifest. Pinning rules are in
[`design.md`](design.md#reproducibility-contract).

## Precise definitions (formal)

TODO in Week 1 — needs LaTeX math; will land in the paper's Section 3.
