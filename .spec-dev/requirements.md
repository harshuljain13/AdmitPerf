# Requirements: AdmitPerf MVP — base of all components

*Drafted 2026-09-13. Status: awaiting review.*

## Problem Statement

AdmitPerf has a frozen Adapter API v0 and five C4 diagrams describing a system, but
[`docs/status.md`](../docs/status.md) lists the runner, state cache, metrics collector,
results bundle writer, engine adapters, and trace loaders as **empty**. Nothing can execute
a single admission decision end to end.

Two people are blocked by this. A **policy author** cannot test a policy against anything,
because no substrate runs it. An **ops engineer** cannot drop the library in front of a
fleet, because the registry is a hardcoded dict they cannot extend from an installed wheel.

The MVP is the thinnest vertical slice that makes one request flow all the way through every
component seam, with no GPU and no network.

## Goals

- **G1 — One end-to-end run, on two substrates.** `admitperf run` takes a workload, a
  policy, and an engine, and produces a results bundle. Every component in
  [`03-component.mmd`](../docs/architecture/03-component.mmd) is exercised. The same command
  works against `ReplayEngine` (CI, no GPU) and `VllmEngine` (real fleet, official metrics).
- **G2 — Every seam exists as a protocol.** `Clock`, `StateSource`, `EngineAdapter`,
  `MetricsSink`, `BundleWriter`, `TraceLoader`, `DeferQueue` are defined and injected. A
  later implementation swaps in without touching `Runner`.
- **G3 — Third-party extensible.** A policy in a *separate* pip package registers itself
  without editing any AdmitPerf file.
- **G4 — Deterministic.** The same config run twice produces byte-identical decisions and a
  matching bundle hash. Verified by a test, not by assertion.
- **G5 — Light to install.** `pip install admitperf` pulls the decision path only. Benchmark
  and runtime concerns are extras.
- **G6 — Honest status.** `docs/status.md` and `03-component.mmd` reflect reality when the
  MVP lands.
- **G7 — Real performance numbers come from the engine's own metrics.** A policy's
  performance is measured against **official vLLM/SGLang telemetry**, never against the
  replay model. Two sources, each authoritative for a different thing:
  - **Engine Prometheus scrape** → decision inputs (`kv_cache_usage_perc`,
    `num_requests_running`, `num_requests_waiting`) and fleet counters
    (`num_preemptions_total`, `request_queue_time_seconds`).
  - **Client-side per-request timing** → TTFT, TBT, goodput, deadline hit/miss, attributable
    to the individual request and therefore to the admission decision that let it through.

  Both land in the results bundle, each tagged with its source. Any metric that cannot be
  obtained from either is reported as unavailable rather than estimated.

## Non-Goals (explicitly out of scope)

- **No SGLang adapter.** `VllmEngine` ships in the MVP because real numbers require it.
  SGLang follows once the port is proven against one real engine — its metric names differ
  and must be verified against a live instance, not assumed.
- **No performance claim from `ReplayEngine`.** The replay model exists for determinism, CI,
  and exercising the decision path. It is explicitly **not** calibrated against hardware.
  Every published number comes from `VllmEngine` against official telemetry (G7). The
  bundle records which engine produced it, and a replay-sourced bundle is marked
  non-comparable.
- **No engine patching.** Preemption-*count* comes from `vllm:num_preemptions_total`.
  Preemption-loss in *KV bytes*, as [`docs/metrics.md`](../docs/metrics.md) currently
  defines it, is not exposed by any engine and would need an engine patch — out of scope,
  and the metric must be redefined as count-based or dropped.
- **No published reference policies.** `NoAdmission` plus one trivial threshold policy to
  prove the reject path. No Chronos-, QLM-, or CONCUR-inspired ports — those are Weeks 2–5
  in [`PROPOSAL.md`](../docs/PROPOSAL.md).
- **No runtime middleware.** The `admitperf.runtime` package is scaffolded with its
  protocol but ships no ASGI/Envoy integration.
- **No leaderboard, no real traces, no PyPI publish.**
- **No API changes.** Adapter API v0 is frozen; the MVP builds around it, not through it.

## Success Criteria

1. `make test` runs an end-to-end replay of ≥1000 synthetic requests in under 5 seconds on a
   laptop, no network.
2. Same config, two runs → identical decision log and identical bundle SHA-256.
3. A policy defined in a test fixture package resolves via `get_policy()` with zero edits to
   `src/admitperf/`.
4. `pip install .` brings in no scientific-Python stack; `pip install .[bench]` does.
5. A swap of `ReplayEngine` → a stub `FakeEngine` requires no change to `Runner`.
6. A run produces every metric in [`docs/metrics.md`](../docs/metrics.md) that is
   computable from its engine, and explicitly reports the rest as unavailable.
7. CI runs the whole thing per PR without a GPU (replay path only).
8. Against a real vLLM instance, `admitperf run` produces per-request TTFT/TBT percentiles
   from client-side timing **and** engine-sourced KV/queue/preemption series, with every
   value in the bundle tagged `source: engine | client`.
9. The engine metric mapping is verified against a live vLLM, not assumed: a contract test
   asserts every name AdmitPerf reads is present in a captured real `/metrics` payload.

## Constraints

- **Adapter API v0 is frozen.** `Request`, `SystemState`, `Decision`, `AdmissionPolicy` must
  not change. Anything missing gets composed around them, not added to them.
- **Purity contract.** `decide()` stays a pure function; no clock reads inside policies. This
  forces `Clock` injection rather than ambient time.
- **Python 3.11+**, ruff-clean, mypy strict, existing 7 smoke tests stay green.
- **Contracts already written** in [`docs/design.md`](../docs/design.md) are binding: state is
  pushed on engine tick and never scraped per arrival; DEFER re-decides on `retry_after_ms`
  *or* next tick, whichever is first; reject codes are 429/503/529 by reason.
- Must remain honest about [`docs/scope.md`](../docs/scope.md) Class A — nothing here
  requires patching engine internals.

## Open Questions

- **Resolved 2026-09-13**: execution substrate → **both, behind one `EngineAdapter` port**.
  `ReplayEngine` for determinism and CI; `VllmEngine` for every real number. This also
  settles [`PROPOSAL.md`](../docs/PROPOSAL.md) open question 1: published numbers come from
  official engine telemetry, and replay is never the source of a performance claim.
- **`docs/metrics.md` needs two corrections** discovered while grounding this against the
  working scraper:
  1. TTFT/TBT percentiles cannot come from `vllm:time_to_first_token_seconds_{sum,count}` —
     those are fleet-aggregate counters yielding a mean. Percentiles must be computed
     client-side per request. The doc should say which source owns each metric.
  2. Preemption-loss ratio is defined in KV *bytes*, which no engine exposes. Redefine as
     a count ratio from `vllm:num_preemptions_total`, or drop it.
- How stale may a `SystemState` be before a policy should refuse to decide? module7 used a
  `STALE_CEILING_S` and returned a `no_signal` shed. Needs a default here.
- `vllm:kv_cache_usage_perc` is reported 0–1 by some builds and 0–100 by others (module7
  normalizes defensively). Does the adapter normalize silently, or assert and fail loudly?
- Does `ReplayEngine` need a token-level model (per-token TBT) or is a two-phase
  prefill/decode duration model enough for v0? Affects whether TBT is meaningful at MVP.
- Should the bundle be a directory or a single archive? Affects the reviewer re-execution
  story in [`01-context.mmd`](../docs/architecture/01-context.mmd).
- Where does `DeferQueue` ordering come from — FIFO, or earliest-deadline? A policy might
  reasonably want to influence it, which would imply a sixth protocol.
- Does `SystemState.engine_metrics` stay a free-form dict, or does the capability set need
  to enumerate keys? Free-form is a Liskov hazard; enumerating it is a frozen-API question.
