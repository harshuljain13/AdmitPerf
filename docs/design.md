# Design — contracts

This document owns the **runtime contracts**: how data flows, and what every run must pin
to be reproducible.

It deliberately does not restate the architecture, the scope, or the metric list:

| For... | See |
|---|---|
| Architecture diagrams | [`architecture/`](architecture/) |
| What is in and out of scope | [`scope.md`](scope.md) |
| Metric definitions | [`metrics.md`](metrics.md) |
| Why this project exists | [`motivation.md`](motivation.md) |

## Goal

Run any published admission-control policy against any OpenAI-compatible LLM engine on the
same trace, and report the same metrics. Everything else is a means to that end.

## Data-flow contract

Rendered as a C4 Dynamic sequence in [`architecture/04-dynamic.png`](architecture/04-dynamic.png).

1. `TraceLoader` yields `TraceEvent`s in wall-clock order.
2. The engine pushes `SystemState` on its own tick. The runner reads the **latest cached
   snapshot** — it does not scrape `/metrics` per arrival. Every `SystemState` carries a
   staleness age so a policy can refuse to decide on stale signal.
3. Runner calls `policy.decide(req, state)` → `ADMIT` / `DEFER` / `REJECT`.
4. **ADMIT** — runner dispatches to the engine, streams tokens back, records TTFT / TBT /
   preemption events.
5. **DEFER** — runner holds the request until `retry_after_ms` elapses *or* the next state
   tick arrives, whichever is first, then re-invokes `decide` with fresher state.
6. **REJECT** — runner synthesizes the HTTP response and notifies the policy via `on_complete`.
7. On stream close or preemption, runner calls `on_complete` / `on_preempt`.

### Rejection status codes

The policy chooses the code; the runner transmits it verbatim so the client can tell
transient backpressure from a hard refusal.

| Code | Meaning | Typical `reason` |
|---|---|---|
| `429` | Retry later; capacity is transiently gone | `queue_depth`, `kv_pressure` |
| `503` | Fleet cannot serve this class right now | `no_signal`, `no_headroom` |
| `529` | Refused on purpose; retrying will not help soon | `deadline_unmeetable` |

## Purity contract

`decide()` is a pure function of `(req, state)`. Same inputs, same decision, always — no
clock reads, no unseeded randomness, no hidden mutation of `req` or `state` (both are frozen
dataclasses). This is the property that makes a run reproducible, and it is enforced by a
determinism test in the cross-policy suite.

Policies that need memory across calls (AIMD counters, EWMA windows) keep it on `self` and
expose the fields so tests can pin them.

## Reproducibility contract

- Every run pins: seed, trace source + revision, policy version, engine version, model,
  GPU SKU, warmup config, wall-clock start.
- Every run emits one results file plus one YAML manifest, SHA-256 signed.
- Fidelity check: for each ported policy, `experiments/fidelity/<policy>.md` names the
  original paper's headline number and our reproduction delta. Rules in
  [`policies.md`](policies.md#fidelity-contract).
