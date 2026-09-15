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
Implemented in `core/runner.py`.

0. Before deciding anything, the runner waits for the first successful scrape, so opening
   decisions are made on real signal rather than an empty cache. A snapshot older than
   `max_state_age_s` is not treated as signal at all: the runner sheds with `no_signal`
   rather than letting a policy guess, because a guess recorded as a decision corrupts the
   comparison the run exists to produce.
1. The workload yields `Request`s in arrival order.
2. A background task scrapes the engine every `scrape_interval_s` and updates the cache.
   The arrival path reads the **latest cached snapshot** and never scrapes inline: an HTTP
   round trip per arrival would add latency to the decision and load the engine with
   exactly the traffic admission control exists to shed. Every snapshot is stamped with its
   age, which rides in `engine_metrics` under `admitperf:state_age_s` because frozen API v0
   has no field for it.
3. Runner calls `policy.decide(req, state)` → `ADMIT` / `DEFER` / `REJECT`.
4. **ADMIT** — runner dispatches to the engine, streams tokens back, records TTFT / TBT /
   preemption events.
5. **DEFER** — runner sleeps `retry_after_ms`, then re-invokes `decide` against fresher
   state. Past `max_defers` the verdict becomes `REJECT("defer_exhausted")`, so a request
   a policy keeps holding cannot silently disappear from the run.
6. **REJECT** — runner records the verdict with its HTTP status and the state it was
   decided on, and never contacts the engine.
7. On stream close, the runner records the outcome: TTFT, inter-token gaps, and whether
   the deadline was met.

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

## Where the queue must form

The engine's own queue is the thing being measured, so nothing may queue in
front of it. On the first real deployment the serving platform accepted one
request at a time, so load queued at its proxy: `num_requests_running` sat at
1, `num_requests_waiting` at 0, and latency climbed while the admission signal
stayed flat. The benchmark was wired correctly end to end and was measuring the
platform rather than the engine.

Any provider must therefore accept far more concurrent requests than the engine
will admit, so `max_num_seqs` is the only bottleneck. `max_concurrent_inputs`
in the config exists for this, and a run records `signal_was_healthy` so a
degraded one is flagged rather than read as a result.

## The unit of comparison is the deployment

Policies are comparable only when they faced the same engine on the same
hardware. The harness enforces that by construction: `infra up` and `bench run`
are separate commands, the server is started once, and every policy runs
against it without re-provisioning.

A sweep (`bench sweep`) varies the deployment rather than breaking that rule.
Each matrix entry is provisioned, every policy runs against it, and it is torn
down before the next. Results are filed per deployment and `bench compare`
prints one table each, refusing to pool them — a single table mixing hardware
would report the machine as if it were the policy.

## Reproducibility contract

- Every run pins, in `manifest.json`: policy, engine, endpoint, KV scale as probed,
  workload name/size/rate/seed, and the provisioning session (model, GPU, provider).
- Every run emits `summary.json`, `decisions.jsonl` and `outcomes.jsonl` alongside it.
  A directory rather than an archive, so a reader can diff two runs and grep the
  decisions.
- Fidelity check: for each ported policy, `experiments/fidelity/<policy>.md` names the
  original paper's headline number and our reproduction delta. Rules in
  [`policies.md`](policies.md#fidelity-contract).
