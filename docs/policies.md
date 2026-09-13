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
| `NoAdmission` (P0 baseline) | — | A | none (FCFS + KV full) | request | ✗ | ✗ | ✗ |
| `ChronosThresholdWCRT` | Chronos, Frontiers Comp Sci 1873627 | A | TTFT/TBT deadline bound | request | ✓ | ✗ | ✗ |
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
