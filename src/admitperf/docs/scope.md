# Scope

## What AdmitPerf is not

- **Not a serving engine.** Sits above vLLM / SGLang / TRT-LLM, not beside them.
- **Not a scheduling algorithm.** Policies here are reference ports, not novel contributions.
- **Not a production admission controller (yet).** Reject/defer semantics are measurement-first; the library-in-front-of-fleet mode is experimental until validated by an adopter.
- **Not a general serving benchmark.** LLMPerf, GenAI-Perf, GuideLLM, Etalon, and MLPerf Inference already measure throughput and latency. None of them treat the *admission decision* as the experimental axis. That axis is the entire scope here.

## Which policies fit behind this API

Published work labelled "admission control" splits into two classes; only one fits behind AdmitPerf's plugin API.

### Class A — ingress-shaped (portable, no engine changes)

A pure function of `(request, observable fleet state) → decision`, running in front of the fleet and reading engine metrics.

Examples that fit:
- **Chronos** (formal WCRT bound)
- **QLM** (KV pressure + LP over virtual queues)
- **Fluid-WAIT** (scalar threshold)
- **Token-budget** rejection
- **Chronos-inspired threshold WCRT** (the first port planned for Week 2)
- The null baseline (`NoAdmission`)

### Class B — batch-formation (not portable)

Changes how batches are *formed* inside the engine. Porting these requires maintaining a vLLM fork — thousands of lines of scheduler internals.

Examples that do NOT fit:
- **FairBatching** (prefill/decode interleaving)
- **FastServe** (iteration-level preemption + KV offload; also runs on SwiftTransformer, not vLLM)
- **CONCUR** (agent-level KV-congestion feedback, a batch-level controller)

See [`prior-art/feasibility_audit.md`](prior-art/feasibility_audit.md).

## The fidelity rule for reference ports

**AdmitPerf serves Class A.** Where a Class B idea matters to the story, we implement an *ingress approximation* and label it as such:

- ✅ `"agent-level admission in the spirit of CONCUR"`
- ❌ `"CONCUR"` (that would claim a faithful port we cannot verify)

That distinction is load-bearing. Claiming a faithful port we cannot verify is the fastest way to lose a reviewer.
