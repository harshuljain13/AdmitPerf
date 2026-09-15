# Policies — the roster

This document owns the **concrete policy roster** and the **fidelity contract** that governs
ports. The taxonomy behind the `Class` column (what is portable and what is not, and the
naming rule) is owned by [`scope.md`](scope.md) — read that first.

Roster size is not settled; see [`PROPOSAL.md`](PROPOSAL.md#7-open-questions) open question 2.

## Candidate roster

`Class A` ports run behind the adapter API unchanged. `Class B` sources cannot be ported —
where one earns its place, we ship an **ingress approximation** under an `-inspired` name and
never under the paper's system name.

| Planned policy | Source | Class | Signal | Unit | SLO-aware | Fair | Agent-aware |
|---|---|---|---|---|---|---|---|
| `NoAdmission` (P0 baseline) | — | A | none | request | ✗ | ✗ | ✗ |
| `QueueDepth` ✅ built | derived | A | `num_requests_waiting` | request | ✗ | ✗ | ✗ |
| `QueueDepthDefer` ✅ built | derived | A | `num_requests_waiting` | request | ✗ | ✗ | ✗ |
| `KVThreshold` ✅ built | derived | A | `kv_cache_usage_perc` | request | ✗ | ✗ | ✗ |
| `ChronosInspiredWCRT` ✅ built | Chronos, Frontiers Comp Sci 1873627 | **A → approximation** | predicted response time vs deadline | request | **✓** | ✗ | ✗ |
| `QLMInspired` | QLM, SoCC 2024 | A | KV pressure + virtual-queue LP | request | ✓ | ✗ | ✗ |
| `FluidWaitThreshold` | Fluid-WAIT, arXiv:2504.11320 | A | queue + KV scalar threshold | request | partial | ✗ | ✗ |
| `TokenBudget` | derived (survey Table 2) | A | offered tokens vs capacity | request | ✓ | ✗ | ✗ |
| `ConcurInspiredAgentAdmission` | CONCUR, arXiv:2601.22705 | **B → approximation** | agent-level KV congestion, observed at ingress | **agent** | ✗ | ✗ | **✓** |

`ConcurInspiredAgentAdmission` is the load-bearing case for the fidelity rule. CONCUR itself
is a batch-level controller whose signal is not visible at ingress, so this is *not* CONCUR.
It is session-granularity admission in the same spirit, and every mention of it — in code,
docs, and paper — carries the qualifier.

## Not portable (Class B, excluded)

Excluded because porting them means maintaining a vLLM fork. See
[`prior-art/feasibility_audit.md`](prior-art/feasibility_audit.md).

- **FairBatching** (arXiv:2510.14392) — rewrites prefill/decode batch formation.
- **FastServe** (NSDI 2026) — iteration-level preemption + KV offload, and it runs on
  SwiftTransformer, not vLLM.

## Considered, not scheduled

- **Aqua** (arXiv:2407.21255) — adjacent; preemption, not admission.
- **Service-Induced Congestion** (arXiv:2606.15555) — modeling paper, hard to reproduce as a policy.
- **ProServe** (arXiv:2512.12928) — very recent, fidelity risk.
- **SOLA** (MLSys 2025) — may overlap Chronos; revisit if a scheduled port fails fidelity.
- **Flow-controlled scheduling** (arXiv:2604.11001) — theory; evaluate if a scheduled port fails fidelity.

## Deadline-aware vs threshold

`ChronosInspiredWCRT` is the first policy here that reads the *request* rather
than only the fleet. Threshold policies refuse past a busyness level and cannot
distinguish an interactive request from a batch one; this predicts how long
*this* request would take and compares that to *its* deadline, so the same
fleet state produces different answers for different SLO classes.

Measured against the fake engine at four-way concurrency:

| queue depth | interactive (500ms) | streaming (2s) | batch (30s) |
|---|---|---|---|
| 0 | reject | admit | admit |
| 8 | reject | reject | admit |
| 60 | reject | reject | reject |

Its rejections are typed — `deadline_unmeetable` for a first-token miss,
`tbt_unmeetable` for streaming smoothness — so a results bundle records which
promise could not be kept.

**It is named "inspired" deliberately.** The published analysis derives sound
worst-case bounds from per-task knowledge: arrival periods, execution-time
bounds, the state of every queued task. A Prometheus endpoint reports counts,
so the queue ahead is characterised by its size and a mean. The reasoning has
the shape of the paper's; the guarantee does not, and per the fidelity rule
below that forbids the bare name.

## Choosing a signal

Measured on real hardware (see [`results.md`](results.md)): on Qwen2.5-0.5B
capped at four concurrent sequences, `kv_cache_usage_perc` never exceeded 0.005
while the queue reached 24 deep. The KV cache dwarfed what four short sequences
could fill, so the binding constraint was the concurrency cap.

A policy reading the wrong signal for its regime does not fail loudly. It sees a
flat line and degrades into admit-everything, scoring identically to the
baseline while appearing to work. Roughly:

| Regime | Binding constraint | Signal that moves |
|---|---|---|
| Small model, short contexts, low `max_num_seqs` | concurrency cap | `waiting_requests` |
| Large model, long contexts, large batches | KV memory | `kv_used_fraction` |
| Deadline-heterogeneous traffic | SLO slack | request deadlines |

This is why `requires` exists: a policy declares its signal, and the harness
refuses to start against an engine that cannot provide it.

## Fidelity contract

For each ported policy, `experiments/fidelity/<policy>.md` records:

1. The paper's headline number (e.g. *"CONCUR reports 4.09× throughput on Qwen3-32B agentic batch"*).
2. Our reproduction number.
3. Delta (%).
4. Notes on any deviation from the original spec.
5. Author correspondence log.

**Accept threshold**: within ±20% of the paper's headline. Outside that we either debug,
contact the authors, or drop the policy from the paper.

An ingress approximation is exempt from the numeric threshold — it is not claiming to
reproduce the source — but it still files the same document, stating explicitly what was
preserved and what was dropped.

## Author outreach

Draft email template lives in `author_outreach.md` (TODO). Contact list drafted in Week 1.
