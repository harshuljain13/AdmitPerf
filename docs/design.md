# AdmitPerf — Design

## Goal

Run any published admission-control policy against any OpenAI-compatible LLM engine on the same trace, and report the same metrics. Everything else is a means to that end.

## Architecture (one page)

```
┌──────────────┐   TraceEvent stream   ┌──────────────┐
│ TraceLoader  │──────────────────────>│              │
└──────────────┘                       │              │
                                       │              │   decide()
┌──────────────┐   SystemState         │   Harness    │──────────>┌──────────────────┐
│    Engine    │<─────────────────────>│   (Runner)   │           │ AdmissionPolicy  │
│   (vLLM,     │   /v1/completions     │              │<──────────│   (CONCUR, etc)  │
│   SGLang,    │                       │              │ decision  └──────────────────┘
│   TRT-LLM)   │                       │              │
└──────────────┘                       │              │
                                       │              │──────────>┌──────────────────┐
                                       │              │  events   │  MetricRegistry  │
                                       └──────────────┘           └──────────────────┘
                                                                          │
                                                                          v
                                                                    results/*.parquet
```

## Data-flow contract

1. `TraceLoader` yields `TraceEvent`s in wall-clock order.
2. Harness reads engine `/metrics` on every event to build a fresh `SystemState`.
3. Harness calls `policy.decide(req, state)` → `ADMIT` / `QUEUE` / `REJECT`.
4. ADMIT: harness dispatches the request to the engine, streams tokens back, records TTFT / TBT / preemption events.
5. QUEUE: harness holds the request; re-invokes `decide` on the next state update.
6. REJECT: harness returns a synthesized `429`; policy is notified via `on_complete`.
7. On stream close / preemption, harness calls `on_complete` / `on_preempt`.

## Non-goals

- Building a serving engine (delegates to vLLM/SGLang/TRT-LLM).
- Routing / load balancing across replicas (single-replica scope).
- Model-side optimizations (quantization, distillation).
- KV cache management as a first-class knob (only observed via engine metrics).

## Reproducibility contract

- Every run pins: seed, trace source + revision, policy version, engine version, model, GPU SKU, warmup config, wall-clock start.
- Every run emits one Parquet results file + one YAML manifest.
- Fidelity check: for each ported policy, `experiments/fidelity/<policy>.md` names the original paper's headline number and our reproduction delta.
