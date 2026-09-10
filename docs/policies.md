# Policies — Baseline Cluster

Six policies for the paper. Cut list at bottom.

| Policy | arXiv / venue | Signal | Unit | Online | SLO-aware | Fair | Agent-aware |
|---|---|---|---|---|---|---|---|
| vLLM default | (no paper) | none (FCFS + KV full) | request | ✓ | ✗ | ✗ | ✗ |
| CONCUR | arXiv:2601.22705 | KV pressure | **agent** | ✗ (batch) | ✗ | ✗ | **✓** |
| Fluid-WAIT | arXiv:2504.11320 | queue + KV threshold | request | ✓ | partial | ✗ | ✗ |
| FairBatching | arXiv:2510.14392 | fairness + SLO | request | ✓ | ✓ | **✓** | ✗ |
| Chronos | Frontiers Comp Sci 1873627 | TTFT/TBT deadlines | request | ✓ | **✓** | ✗ | ✗ |
| FastServe | NSDI 2026 | skip-join MLFQ + preemption | request | ✓ | ✓ | ✗ | ✗ |

## Cut list (may add later)

- Aqua (arXiv:2407.21255) — adjacent, preemption not admission.
- Service-Induced Congestion (arXiv:2606.15555) — position-ish, hard to reproduce as a policy.
- ProServe (arXiv:2512.12928) — very recent, fidelity risk.
- SOLA (MLSys 2025) — may overlap FairBatching + Chronos.
- Flow-controlled scheduling (arXiv:2604.11001) — evaluate if any of the six above fail fidelity.

## Fidelity contract

For each policy: `experiments/fidelity/<policy>.md` records
1. Paper's headline number (e.g., "CONCUR reports 4.09x throughput on Qwen3-32B agentic batch").
2. Our reproduction number.
3. Delta (%).
4. Notes on any deviations from the original spec.
5. Author correspondence log.

Accept threshold: within ±20% of paper's headline. If a policy falls outside that, we either debug, contact authors, or drop from the paper.

## Author outreach

Draft email template lives in `docs/author_outreach.md` (TODO). Contact list drafted in Week 1.
