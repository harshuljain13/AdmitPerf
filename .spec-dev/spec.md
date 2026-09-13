# Spec: AdmitPerf MVP — base of all components

Status: **Draft** — decisions made, open items flagged inline
Last updated: 2026-09-13

## Summary

Build the thinnest vertical slice in which one request flows through every component seam in
[`03-component.mmd`](../docs/architecture/03-component.mmd). Every seam is a `Protocol`
injected into `Runner`; nothing is constructed inside it. Two `EngineAdapter` implementations
ship: `ReplayEngine` (deterministic, CI, no GPU) and `VllmEngine` (real fleet, official
Prometheus telemetry). Published numbers come only from the latter.

The package splits so that `pip install admitperf` buys the decision path alone, and policies
in third-party packages register without editing AdmitPerf source.

---

## Design Decisions

### D1: Clock is an injected protocol, not ambient time
**Options**: (A) `time.monotonic()` inside `Runner`; (B) injected `Clock` protocol with
`WallClock` and `VirtualClock`.
**Chosen**: B.
**Why**: `04-dynamic.mmd` claims one sequence serves both replay and runtime. That is only
true if time is a dependency. Replay needs virtual time that jumps to the next event (1000
requests in <5s); live needs wall time. With ambient time, replay runs in real seconds and
determinism is impossible. This is the single highest-leverage decision in the MVP.

```python
class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep_until(self, t: float) -> None: ...
```

`VirtualClock.sleep_until` advances a counter and returns immediately; `WallClock` actually
sleeps. Nothing else in the system knows which it has.

### D2: The frozen API stays frozen; missing concepts compose around it
**Options**: (A) add `staleness_age` / `capabilities` to `SystemState`; (B) keep v0 frozen
and carry extras in `engine_metrics`.
**Chosen**: B.
**Why**: v0 was frozen 2026-09-11 and `CONTRIBUTING.md` forbids changing it. `docs/design.md`
promises a staleness age that the dataclass does not have, so it goes in the free-form dict
under a reserved `admitperf:` prefix:

| Reserved key | Meaning |
|---|---|
| `admitperf:state_age_s` | seconds since the tick that produced this snapshot |
| `admitperf:engine` | `replay` \| `vllm` |

**Revisit**: this is a wart. If more reserved keys accrue, that is the signal to cut API v1
with proper fields rather than keep overloading a dict.

### D3: Registry uses entry points, not a hardcoded dict
**Options**: (A) keep `POLICIES` dict; (B) entry points + decorator.
**Chosen**: B, with the dict retained as the built-in seed.
**Why**: G3. A third party who installs the wheel cannot edit `policies/__init__.py`. Today
the package is closed to precisely the extension it exists to enable — an Open/Closed
violation at the core of the value proposition.

```python
# third-party package's pyproject.toml
[project.entry-points."admitperf.policies"]
my_policy = "my_pkg.policies:MyPolicy"
```

`get_policy()` resolves built-ins first, then entry points. Duplicate names are an error,
not a silent override.

### D4: Two engines behind one port; replay never produces a number
**Options**: (A) replay only; (B) live only; (C) both behind one port.
**Chosen**: C.
**Why**: performance comparison between algorithms requires official engine telemetry
(G7) — a simulator cannot tell you whether Chronos beats QLM. But CI, determinism tests, and
policy unit tests cannot depend on a GPU. Both needs are real and neither subsumes the other.

The bundle records `engine: replay|vllm`. A replay-sourced bundle is stamped
`comparable: false` so a simulated number can never be mistaken for a measured one.

### D5: Metrics have two sources, and each value says which
**Options**: (A) engine Prometheus only; (B) client-side only; (C) both, tagged.
**Chosen**: C.
**Why**: forced by the data. `vllm:time_to_first_token_seconds_{sum,count}` are
fleet-aggregate counters — they yield a mean, not the p50/p95/p99 that
[`docs/metrics.md`](../docs/metrics.md) requires, and they cannot be attributed to an
individual request or therefore to an admission decision.

| Metric | Source | Mechanism |
|---|---|---|
| TTFT, TBT (percentiles) | **client** | timestamps on the streaming response |
| goodput, deadline hit/miss | **client** | per-request, compared to `Request` deadlines |
| admit/defer/reject counts | **harness** | the runner owns the decision |
| KV pressure series | **engine** | `vllm:kv_cache_usage_perc` |
| queue depth, running | **engine** | `vllm:num_requests_{waiting,running}` |
| preemption count | **engine** | `vllm:num_preemptions_total` |
| preemption-loss in KV bytes | **unavailable** | no engine exposes it — see D6 |

### D6: Metrics that cannot be measured are reported unavailable, never estimated
**Why**: `docs/metrics.md` defines preemption-loss as a ratio of KV *bytes*, which requires
an engine patch the feasibility audit rules out of scope. The MVP emits a count-based
`preemption_count` and records `preemption_loss_bytes: unavailable` with a reason string.

**Action on docs**: `metrics.md` must be corrected — it currently promises two things
(TTFT percentiles from engine metrics, byte-level preemption loss) that are not obtainable.

### D7: Package split along install-weight lines
**Options**: (A) one package; (B) split by concern.
**Chosen**: B.
**Why**: Interface Segregation, expressed as dependencies. An ops engineer wanting admission
control must not transitively install pandas and a bundle writer.

```
admitperf.core       Protocols, registry, Runner, clock, state cache, defer queue   (stdlib + click)
admitperf.engines    ReplayEngine (stdlib) · VllmEngine (httpx)
admitperf.bench      trace loaders, metrics collector, bundle writer                [bench]
admitperf.runtime    ASGI middleware — protocol only in MVP                         [runtime]
```

`core` must not import `bench`. Enforced by an import-linter test, not by good intentions.

### D8: Capability declaration makes Liskov substitution safe
**Why**: `SystemState` fields are `None` when an engine does not expose them. A policy needing
`kv_used_fraction` would silently misbehave rather than fail. Each engine declares what it
provides; each policy declares what it requires; the runner checks **once at wiring time**.

```python
class KVThreshold(AdmissionPolicy):
    requires = frozenset({"kv_used_fraction"})
```

Fails fast with a readable error instead of degrading mid-run.

### D9: Stale state fails loudly in benchmark mode, sheds in runtime mode
**Decision made on your behalf — revisit.**
**Why**: module7 shed with `no_signal` past `STALE_CEILING_S`, correct for production where
serving something beats serving nothing. In a benchmark, deciding on stale signal silently
corrupts the comparison. So: `runtime` → shed (`503 no_signal`); `bench` → raise and abort
the run. Default ceiling 5.0s, configurable.

### D10: `kv_cache_usage_perc` scaling asserts rather than normalizes
**Decision made on your behalf — revisit.**
**Why**: some builds report 0–1, others 0–100; module7 normalized defensively with
`kv / 100.0 if kv > 1.0 else kv`. That heuristic is wrong for a genuine 100% full cache, and
a silent 100× error in KV pressure would corrupt every decision in the run. The adapter
probes scale once at startup, records it in the manifest, and asserts thereafter. Loud
failure beats a quietly wrong benchmark.

### D11: DeferQueue is FIFO in v0, behind a protocol
**Why**: EDF or policy-influenced ordering is defensible, but ordering changes results, and
the MVP should not bake in an unexamined choice. FIFO is the honest baseline; the protocol
lets a later experiment swap it and attribute the difference.

### D12: ReplayEngine uses a two-phase duration model, not token-level
**Why**: prefill duration from input tokens, decode duration from output tokens, fixed
service rates, an explicit KV occupancy budget driving admission pressure and preemption.
Token-level simulation would imply TBT fidelity it does not have. Consequence: replay reports
TTFT (synthetic, non-comparable) and does **not** report TBT at all.

---

## Architecture

```
CLI  ──builds──▶  Runner(clock, states, engine, policy, workload, metrics, defers)
                     │
   ┌─────────────────┼──────────────────┬─────────────────┐
   ▼                 ▼                  ▼                 ▼
StateSource     EngineAdapter      MetricsSink       DeferQueue
   │                 │                  │
   │            ReplayEngine       BundleWriter
   │            VllmEngine
   └── fed by engine tick
```

### The protocols (all in `core`)

```python
class Clock(Protocol):
    def now(self) -> float: ...
    async def sleep_until(self, t: float) -> None: ...

class StateSource(Protocol):
    def current(self) -> SystemState: ...      # cached; never does I/O
    def capabilities(self) -> frozenset[str]: ...

class EngineAdapter(Protocol):
    async def submit(self, req: Request) -> RequestOutcome: ...
    def subscribe_ticks(self, cb: Callable[[SystemState], None]) -> None: ...
    def capabilities(self) -> frozenset[str]: ...

class MetricsSink(Protocol):
    def record_decision(self, req, decision, state) -> None: ...
    def record_outcome(self, req, outcome: RequestOutcome) -> None: ...
    def record_tick(self, state: SystemState) -> None: ...

class DeferQueue(Protocol):
    def push(self, req: Request, retry_at: float) -> None: ...
    def due(self, now: float) -> list[Request]: ...

class BundleWriter(Protocol):
    def write(self, run: RunManifest) -> Path: ...
```

`RequestOutcome` is new and MVP-owned (not part of frozen v0): `request_id`, `ttft_ms`,
`tbt_ms_list`, `total_ms`, `output_tokens`, `status`, `met_deadline`.

### Runner loop

Per [`docs/design.md`](../docs/design.md), unchanged in substance:

1. Drain `DeferQueue.due(clock.now())` — deferred requests re-decide **before** new arrivals.
2. Pull next `TraceEvent`; `clock.sleep_until(event.time)`.
3. `state = states.current()` — cache read, no I/O.
4. Guard staleness per D9.
5. `decision = policy.decide(req, state)`; record it.
6. Dispatch: ADMIT → `engine.submit`; DEFER → `defers.push(req, now + retry_after_ms/1000)`;
   REJECT → record with HTTP code by reason (429/503/529 per `design.md`).
7. On completion, `metrics.record_outcome` and `policy.on_complete`.

DEFER re-decides on `retry_after_ms` **or** next tick, whichever is first — as `design.md`
specifies.

## Results bundle

A directory, not an archive — reviewers diff directories; opaque blobs discourage inspection.

```
runs/<run_id>/
├── manifest.yaml      config, seed, engine, versions, kv-scale probe, comparable: bool
├── decisions.jsonl    one row per decision: req, kind, reason, state snapshot ref
├── outcomes.jsonl     one row per completed request (client-sourced timings)
├── ticks.jsonl        engine state series
├── metrics.json       computed aggregates, each tagged source: engine|client|harness
└── SHA256SUMS
```

Determinism (G4) = identical `SHA256SUMS` across two replay runs of one config.

## Edge cases

| Case | Handling |
|---|---|
| Engine unreachable at startup | fail fast with the URL tried; never start a partial run |
| Scrape fails mid-run | keep last snapshot, grow `state_age_s`; D9 governs |
| Policy raises | abort the run, write a partial bundle marked `aborted: true` |
| `DEFER` without `retry_after_ms` | already a `ValueError` in frozen v0 |
| Request deferred forever | `max_defer_count` (default 100) → forced REJECT `defer_exhausted` |
| Trace exhausted, requests in flight | drain in-flight, then finalize |
| Replay + `--engine vllm` in CI | CI passes `--engine replay`; vLLM tests marked `integration` |
| Duplicate policy name from entry point | hard error listing both sources |

## Not in v1

Everything in `requirements.md` non-goals, plus: SGLang adapter, ASGI middleware body,
leaderboard, real trace corpora, PyPI publish, `VirtualClock` concurrency beyond a single
runner task.

## Dependencies

`core`: stdlib + `click`. `engines`: `httpx`. `bench`: `pyyaml`; numpy only if percentiles
warrant it (stdlib `statistics.quantiles` likely suffices — prefer it).

## Open questions resolved

- Substrate → D4, both engines, replay never comparable.
- Bundle format → directory (above).
- DeferQueue ordering → D11, FIFO behind a protocol.
- Staleness → D9. **Flagged for your review.**
- KV scaling → D10. **Flagged for your review.**
- Token-level replay → D12, no; TBT unavailable in replay.
- `engine_metrics` free-form → D2, reserved `admitperf:` prefix; revisit at API v1.
