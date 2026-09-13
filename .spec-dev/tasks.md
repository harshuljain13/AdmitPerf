# Tasks: AdmitPerf MVP — base of all components

Legend: `[ ]` not started · `[x]` in progress · `[✓]` done

Sequencing rule: each phase ends with something runnable and tested. No phase depends on a
later phase's code. Every task carries its definition of done.

---

## Phase 1: Foundation — protocols and wiring

Goal: every seam exists as a `Protocol`, nothing is constructed inside `Runner`, and a
third-party policy can register. No behaviour yet.

- [✓] **1.1 Package split** — create `core/`, `engines/`, `bench/`, `runtime/` subpackages;
  move frozen API into `core/` re-exported from its current path so nothing breaks.
  *Done when*: existing 7 smoke tests pass unchanged; `from admitperf import AdmissionPolicy`
  still works.
- [✓] **1.2 Protocols module** (`core/ports.py`) — `Clock`, `StateSource`, `EngineAdapter`,
  `MetricsSink`, `DeferQueue`, `BundleWriter`, plus the `RequestOutcome` dataclass.
  *Done when*: mypy strict passes; every protocol is `@runtime_checkable`.
- [✓] **1.3 Clock implementations** (`core/clock.py`) — `WallClock`, `VirtualClock` (D1).
  *Done when*: test proves `VirtualClock` advances without real sleeping and is monotonic.
- [✓] **1.4 Entry-point registry** (D3) — `get_policy()` resolves built-ins then
  `admitperf.policies` entry points; duplicate names raise.
  *Done when*: a fixture package in `tests/fixtures/` registers a policy discovered with zero
  edits to `src/admitperf/`.
- [✓] **1.5 Capability declaration** (D8) — `requires` on `AdmissionPolicy`, `capabilities()`
  on engine/state source, `check_compatibility()` called once at wiring.
  *Done when*: policy requiring `kv_used_fraction` against an engine lacking it raises a
  readable error naming both sides.
- [✓] **1.6 Import-linter test** (D7) — assert `core` never imports `bench`/`engines`.
  *Done when*: the test fails if someone adds the import.

## Phase 2: Replay engine and the runner loop

Goal: first end-to-end run. Decisions happen, requests complete, nothing is measured yet.

- [ ] **2.1 `ReplayEngine`** (D12) — two-phase prefill/decode duration model, KV occupancy
  budget, preemption when the budget is exceeded, tick emission on a fixed interval.
  *Done when*: deterministic across two runs with the same seed; declares capabilities.
- [ ] **2.2 `StateCache`** — holds the latest tick, computes `admitperf:state_age_s` (D2),
  never performs I/O in `current()`.
  *Done when*: test proves `current()` does no I/O and age grows with the clock.
- [ ] **2.3 `FifoDeferQueue`** (D11) — `push`/`due`, plus `max_defer_count` → forced REJECT
  `defer_exhausted`.
  *Done when*: a request deferred 100× is rejected, not looped forever.
- [ ] **2.4 `Runner`** — the 7-step loop from spec; deferred requests re-decide before new
  arrivals; staleness guard per D9 (mode-dependent).
  *Done when*: `NoAdmission` + `ReplayEngine` + synthetic workload completes 1000 requests.
- [ ] **2.5 Synthetic workload** (`bench/workloads/poisson.py`) — Poisson arrivals, 3 SLO
  classes, seeded; implements the existing `TraceLoader` ABC.
  *Done when*: same seed → identical event stream.
- [x] **2.6 Threshold policy** — trivial KV-threshold policy to exercise the REJECT path.
  *Done when*: produces a non-zero reject count under load; the three CONTRIBUTING tests pass.

## Phase 3: Measurement and the bundle

Goal: a run produces numbers, provenance-tagged, and a reproducible bundle.

- [ ] **3.1 `MetricsCollector`** (D5) — record decisions, outcomes, ticks; tag every value
  `source: engine|client|harness`.
  *Done when*: aggregates match a hand-computed fixture.
- [ ] **3.2 Percentiles** — TTFT/TBT p50/p95/p99 from client-side per-request timings via
  stdlib `statistics.quantiles`.
  *Done when*: verified against a known distribution; no numpy dependency added.
- [ ] **3.3 Unavailable-metric reporting** (D6) — `preemption_loss_bytes: unavailable` with
  a reason string; never estimated.
  *Done when*: bundle shows the field with its reason, and `preemption_count` is populated.
- [ ] **3.4 `BundleWriter`** — the directory layout from spec + `SHA256SUMS`; `comparable:
  false` stamped for replay-sourced runs (D4).
  *Done when*: two identical replay runs produce identical `SHA256SUMS` (**G4**).
- [ ] **3.5 Determinism test** — the headline guarantee, as a test.
  *Done when*: 1000 requests, two runs, byte-identical, under 5s (**success criterion 1**).

## Phase 4: Real engine, real metrics

Goal: numbers that can be published. This is the phase that makes algorithm comparison possible.

- [ ] **4.1 Prometheus parser** (`engines/prometheus.py`) — port the proven regex parser
  from module7 `gateway/scrape.py`.
  *Done when*: parses a captured real `/metrics` payload fixture.
- [ ] **4.2 Metric-name mapping + contract test** (**success criterion 9**) — map
  `vllm:kv_cache_usage_perc`, `num_requests_{running,waiting}`, `num_preemptions_total`,
  `request_queue_time_seconds_*` into `SystemState`.
  *Done when*: a test asserts every name AdmitPerf reads exists in the captured payload, so a
  vLLM rename breaks CI instead of silently zeroing a signal.
- [ ] **4.3 KV scale probe** (D10) — detect 0–1 vs 0–100 once at startup, record in manifest,
  assert thereafter.
  *Done when*: a payload switching scale mid-run raises rather than corrupting the series.
- [ ] **4.4 `VllmEngine.submit`** — OpenAI-compatible streaming call with client-side TTFT/TBT
  timestamping (D5).
  *Done when*: against a stub HTTP server, TTFT and per-token gaps are measured correctly.
- [ ] **4.5 Tick loop** — background scrape at `SCRAPE_INTERVAL_S` feeding `StateCache`;
  scrape failure grows staleness rather than crashing.
  *Done when*: killing the stub server triggers D9 behaviour for the configured mode.
- [ ] **4.6 Integration test** — marked `integration`, skipped without a live vLLM.
  *Done when*: CI green without a GPU; documented how to run it with one.

## Phase 5: CLI, docs, honesty pass

- [ ] **5.1 `admitperf run`** — wire config YAML → objects (dependency inversion lives here,
  not in `Runner`); `--engine replay|vllm`.
  *Done when*: the README quickstart command actually runs.
- [ ] **5.2 `policies` / `traces` commands** — list built-ins *and* entry-point-discovered.
  *Done when*: the fixture policy appears in `admitperf policies`.
- [ ] **5.3 Correct `docs/metrics.md`** (D6) — TTFT/TBT percentiles are client-sourced;
  preemption-loss redefined as count-based or dropped.
  *Done when*: no metric is promised that the MVP cannot produce.
- [ ] **5.4 Update `docs/status.md` + `03-component.mmd`** (**G6**) — green the boxes that
  are now real.
  *Done when*: diagram status matches reality; `make diagrams` re-rendered.
- [ ] **5.5 Fix stale lineage paths** — `module6-admission-and-routing` →
  `module7-admission-and-routing` in `docs/lineage.md` and README.
  *Done when*: link checker passes.
- [ ] **5.6 CI workflow** — GitHub Actions: lint, mypy, tests, `make diagrams-check`.
  *Done when*: green on a PR without a GPU (**success criterion 7**).

---

## Blocked by

- Phase 3 blocked by 2.4 (`Runner` must emit events before they can be collected).
- 4.2 blocked by capturing a real vLLM `/metrics` payload — **needs a live instance once**.
  Until then, use module7's observed metric names and mark the fixture provisional.
- 5.4 blocked by Phases 2–4 landing.

## Notes

- Phase 1 is pure structure — boring but it is where the SOLID properties are won or lost.
- Phase 2 ends with the first end-to-end run; that is the real milestone.
- Phase 4 is the phase that satisfies "official metrics from vLLM/SGLang." Everything before
  it is scaffolding to make it measurable and reproducible.
- SGLang is deliberately absent; its metric names must be verified against a live instance,
  not assumed.
