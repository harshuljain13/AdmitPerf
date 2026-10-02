# Metrics

This document owns every metric definition. Nothing else in the repo should define one; new
metrics arrive here first (see [`../CONTRIBUTING.md`](../CONTRIBUTING.md)).

## Standard (per request)

- **TTFT** — Time To First Token. p50 / p95 / p99.
- **TBT** — Time Between Tokens (inter-token latency). p50 / p95 / p99.

**A request meets its SLO only if both hold.** Admission control has two jobs — get the
first token out in time, and keep the stream smooth afterwards — and a request that starts
promptly then stalls mid-answer has not been served well. Per-request **p95** inter-token
latency is judged, not the mean, because a mean hides exactly the stalls preemption causes.
- **Throughput** — completed requests per second, regardless of SLO.
- **Goodput** — completed requests per second that met their SLO.
- **GPU utilization** — from engine `/metrics` and, optionally, DCGM.

## Admission-specific

These are the metrics the standard harnesses (LLMPerf, GenAI-Perf, GuideLLM, MLPerf) do not
report, because none of them treat admission as an experimental axis. They are **not claimed
as novel** — prior art for each is noted below, and
[`prior-art/adversarial_review.md`](prior-art/adversarial_review.md) argues the novelty case
is weak. The contribution is reporting them *together, on one substrate*, not inventing them.

- **Offered attainment** *(primary)* — SLO-meeting requests / everything that arrived.
  Rejections count as misses, so a policy cannot improve this by refusing more — only by
  refusing *better*.
- **Served attainment** — SLO-meeting requests / admitted. This flatters shedding: a policy
  refusing 95% and serving the rest perfectly scores 1.00. It is never reported without
  offered attainment beside it. Measured on a real run, a queue-depth limit scored 0.942
  served against 0.400 offered — worse than the uncontrolled baseline's 0.475.
- **Goodput** — SLO-meeting requests per second, as published work reports it. The
  *sustainable* rate, meaning the highest load at which attainment stays above a target,
  needs a load sweep rather than a single run.
- **Wasted output tokens** — tokens generated for requests that missed their promise
  anyway. The clearest statement of what a policy saved by refusing.
- **Reject latency** — p95 time to return a refusal. A fast fail is the point; a slow one
  costs the caller the wait and gives them nothing.
- **Decision lag** — p95 delay between a request being due and its verdict. Doubles as a
  coordinated-omission check: if the load generator falls behind, recorded latencies
  understate what a client would have seen.
- **Admission-decision histogram** — fraction of arrivals that got `ADMIT` / `DEFER` /
  `REJECT`, broken down by `reason`.
- **Defer latency** — wall-clock time a request spent held before its terminal decision.
- **Preemption count** — `vllm:num_preemptions_total`, read from the engine.
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

## Not measurable, and reported as such

Every results bundle carries an `unavailable` block naming these, with the reason. They
are never estimated, because an estimate written next to a measurement is
indistinguishable from one.

- **Preemption loss in KV bytes.** Originally specified here as a ratio of KV bytes
  discarded to KV bytes generated. No engine exposes byte-level preemption accounting;
  `vllm:num_preemptions_total` is a count, not a volume. Getting the ratio would require
  patching the engine, which [`scope.md`](scope.md) rules out. The count is reported
  instead.
- **GPU utilization.** Needs DCGM running alongside the engine. Not collected.

## Not measured (out of scope)

- Model quality (accuracy, task success beyond completion) — orthogonal.
- Cost — implied by GPU-hour, but not first-class.
- Cold-start latency — a separate concern from admission.

## Where each number comes from

Every value in `summary.json` is tagged with its source, because the distinction decides
whether a number can be defended:

| Source | Meaning | Examples |
|---|---|---|
| `client` | Timed here, as the response streamed back | TTFT, inter-token gaps, throughput |
| `harness` | Counted by the admission layer itself | admit/defer/reject counts, reasons |
| `engine` | Read from the engine's own telemetry | KV pressure, queue depth, preemptions |

**Latency percentiles are necessarily client-sourced.** vLLM publishes TTFT and
inter-token latency as `_sum`/`_count` pairs, which yield a running mean over every
request the server has handled since it started. There is no distribution in them, so no
p95 can be recovered, and no value can be attributed to the individual request whose
admission decision is under test — which is the only attribution that matters here.

## Results bundle

Each run writes a directory: `manifest.json` (what was run), `summary.json` (the numbers
above), `decisions.jsonl` (every verdict with the state it was made on), and
`outcomes.jsonl` (per-request timings). Pinning rules are in
[`design.md`](design.md#reproducibility-contract).

## Precise definitions (formal)

TODO — needs LaTeX math; will land in the paper's Section 3.
