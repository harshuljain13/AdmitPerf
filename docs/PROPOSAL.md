# AdmitPerf: A Reproducible Harness for Admission Control in LLM Inference Serving

*Proposal v0.1 — 2026-09-11*
*Authors: Harshul Jain, Dr. Tanmay Sah, Tanya Sah (all independent researchers)*
*Companion to: `../survey/paper.tex` (systematic review, arXiv preprint pending)*

---

## 1. One-line pitch

AdmitPerf is a pluggable-policy benchmark harness that measures **goodput-under-admission** for LLM serving systems across SLO-diverse, multi-tenant, and agent-session workloads — the axis that LLMPerf, GenAI-Perf, GuideLLM, and MLPerf Inference LLM do not cover.

## 2. Motivation (grounded in the survey)

The companion systematic review documents three findings that motivate this work:

1. **9-for-9 no ingress admission** in top-venue LLM serving papers (Orca, Sarathi-Serve, Llumnix, DistServe, Past-Future, Aegaeon, Jenga, AdaServe, PASCAL). Overload is handled downstream (batching, migration, autoscaling), never at ingress.
2. **Zero baseline overlap** across 14 admission-primary papers. No two share a baseline system, engine version, workload, or SLO definition. Cross-paper comparison is impossible from the literature alone.
3. **Empty intersection** `agent-level × online × deadline-aware × fair`. No published entry.

Finding (2) is the direct wedge for AdmitPerf. Findings (1) and (3) motivate the *workload* and *policy adapter* choices below.

## 3. Goals

- **G1** — A policy-adapter API that lets researchers plug an admission policy into vLLM and SGLang without engine-specific glue code.
- **G2** — A workload suite covering (a) request-level Poisson mixes with per-class SLOs, (b) multi-tenant fairness scenarios, (c) agent-session traces with multi-turn and tool-call bursts.
- **G3** — A metric contract centered on **goodput-under-admission** (requests meeting SLO / offered load), with reject/defer accounting as first-class citizens, alongside standard TTFT / TPOT / p99 latency / KV utilization.
- **G4** — Reference implementations for at least six admission policies drawn from the survey: threshold (Chronos-style WCRT), wait-time-aware (SOLA), KV-admission (QLM), token-budget rejection, deadline-based (CONCUR-style), and a no-admission baseline.
- **G5** — One-command reproduction: `admitperf run <config.yaml>` produces a signed results bundle (traces, metrics, environment manifest, policy hash).

## 4. Non-goals

- Not a new *serving engine*. AdmitPerf sits above vLLM / SGLang, not beside them.
- Not a new *scheduling algorithm*. Policies are reference ports, not novel contributions.
- Not a *production admission controller*. Reject/defer semantics are for measurement, not deployment.
- Not a *training benchmark*. Inference only.

## 5. Differentiation

| Harness | Throughput | TTFT / TPOT | Multi-SLO classes | Reject / defer accounting | Policy adapter API | Agent-session traces |
|---|---|---|---|---|---|---|
| LLMPerf | yes | yes | no | no | no | no |
| GenAI-Perf | yes | yes | no | no | no | no |
| GuideLLM | yes | yes | partial | no | no | no |
| MLPerf Inference LLM | yes | yes | no | no | no | no |
| vLLM `benchmarks/` | yes | yes | no | no | no | no |
| **AdmitPerf** | yes | yes | **yes** | **yes** | **yes** | **yes** |

## 6. Architecture (sketch)

```
+-------------------------------------------------------+
|                  admitperf CLI + runner               |
+-------------------------------------------------------+
|  Workload loader   |   Policy adapter   |  Metrics    |
|  (traces + mix)    |   (Python plugin)  |  collector  |
+---------+----------+---------+----------+------+------+
          |                    |                 |
          v                    v                 v
   +--------------+    +--------------+   +------------+
   | Trace store  |    | Engine glue  |   | Results    |
   | (JSONL)      |    | (vLLM/SGLang)|   | bundle     |
   +--------------+    +--------------+   +------------+
```

Policy adapter contract (Python):

```python
class AdmissionPolicy(Protocol):
    def admit(self, req: Request, state: SystemState) -> Decision: ...
    # Decision = ADMIT | REJECT(reason) | DEFER(retry_after_ms)
```

`SystemState` exposes engine-visible signals (queue depth, running-batch tokens, KV free blocks, per-tenant credit) via a stable schema that both vLLM and SGLang backends populate.

## 7. Workload suite

- **W1 request-mix**: Poisson arrivals over three SLO classes (interactive 500ms TTFT / streaming 2s TTFT / batch 30s completion), ShareGPT + LMSYS-Chat-1M prompts.
- **W2 tenant-fairness**: N tenants with heterogeneous rate + priority, adversarial burst scenarios.
- **W3 agent-session**: multi-turn conversations with tool-call latencies stitched in (LangChain-style traces), long-tail context growth.
- **W4 reasoning-heavy**: mixed decode-length workload with hidden reasoning traces (motivated by survey Open Problem 4).

All workloads distributed as JSONL with a schema + validator; both synthetic generators and captured traces where licensing permits.

## 8. Reference policies (v1)

| # | Policy | Source | Signal |
|---|---|---|---|
| P0 | NoAdmission | baseline | — |
| P1 | ThresholdWCRT | Chronos (Frontiers CS 2026) | queue-response bound |
| P2 | WaitTimeAware | SOLA (MLSys 2025) | expected wait |
| P3 | KVAdmission | QLM (SoCC 2024) | KV pressure |
| P4 | TokenBudget | derived from survey Table 2 | offered tokens vs. capacity |
| P5 | DeadlineLP | CONCUR (ICML 2026) | dual-gate LP |

Each is a self-contained plugin (~200-400 LoC) with unit tests and a citation stub.

## 9. Metrics contract

- **Primary**: goodput-under-admission = (SLO-meeting admitted requests) / (offered load)
- **Secondary**: TTFT p50/p95/p99, TPOT p50/p95/p99, KV utilization, tenant-fairness (Jain's index), reject-rate, defer-rate, wasted-work fraction
- **Bundle**: JSON + parquet; environment manifest (engine SHA, GPU, driver, workload seed, policy hash)

## 10. Author split

| Author | Track | First-order deliverables |
|---|---|---|
| Harshul Jain | Systems | CLI, runner, engine glue (vLLM + SGLang), results bundle, Modal automation |
| Dr. Tanmay Sah | Theory + one policy | Metric formalization, P1 (Chronos WCRT) port, queueing-model sanity checks |
| Tanya Sah | Measurement + writing | Workload curation (W1-W4), reproducibility audits, paper draft coordination |

Weekly syncs on Wednesdays. Async coordination via the repo (issues + PRs). All three named as co-first authors; ordering by contribution at freeze.

## 11. Milestones (8 weeks)

| Week | Milestone | Owner |
|---|---|---|
| 1 | Repo scaffold, adapter API v0, W1 loader | Harshul |
| 2 | vLLM engine glue, NoAdmission + ThresholdWCRT policies | Harshul + Tanmay |
| 3 | SGLang engine glue, W2 tenant-fairness workload | Harshul + Tanya |
| 4 | KVAdmission + WaitTimeAware policies | Tanmay |
| 5 | W3 agent-session workload, TokenBudget + DeadlineLP | Tanya + Harshul |
| 6 | Full 6-policy × 4-workload sweep on 4×A100 | Harshul |
| 7 | Cross-policy leaderboard, results bundle v1 | Tanmay + Tanya |
| 8 | Paper draft freeze, arXiv preprint | all |

## 12. Compute plan

- **Dev**: 1×A10G on Modal, ~$0.60/hr, 60 hrs/wk cap. ~$150/wk during weeks 1-5.
- **Sweep**: 4×A100 or 2×H100 on Modal or Lambda for weeks 6-7, ~$800-1200 total.
- **Total budget target**: under $2500.
- Fallback: 1×A10G for smaller models (Llama-3-8B, Qwen3-7B) if the H100 budget slips.

## 13. Venue targets

| Venue | Deadline | Fit | Notes |
|---|---|---|---|
| MLSys 2027 D&B | 2026-10-30 | high | tight (7 weeks); stretch |
| NeurIPS 2027 D&B | 2027-06 | high | comfortable |
| ES-FoMo @ ICML 2027 | ~2027-02 | high | workshop venue for community feedback |
| arXiv preprint | week 8 | required | ships with code + leaderboard |

Primary target: **ES-FoMo @ ICML 2027** for community adoption + **NeurIPS 2027 D&B** for archival credit. MLSys 2027 D&B only if week-6 sweep lands clean.

## 14. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Engine API drift (vLLM/SGLang) | high | pin versions in manifest; adapter shim isolates changes |
| Policy port inaccuracy | medium | each port includes a paper-numbers sanity check |
| Compute overrun | medium | 8B-model fallback path; Modal spot instances |
| Name collision (post-launch) | low | AdmitPerf verified clean on GitHub + Google (2026-09-11) |
| Reviewer critique: "just a benchmark" | medium | leaderboard + survey pairing shows the community wedge |

## 15. Reproducibility posture

- Every run produces a signed bundle (SHA-256) with engine SHA, driver, workload seed, policy hash.
- All policy ports cite the primary source paper with exact operating point.
- CI runs a smoke sweep (1 policy × 1 workload × 100 requests) on every PR.
- Leaderboard is append-only; retractions are marked, not deleted.

## 16. Relationship to prior artifacts

- **`../survey/`** — companion systematic review, provides motivation and taxonomy.
- **This repo (formerly `admitperf/`)** — the harness itself. Directory to be renamed to `admitperf/` at Week 1 milestone.
- **`../private/prior-art/`** — 30-day re-check log, kept current.

## 17. Open questions (to resolve in Week 1)

- Whether to include a `Splitwise`-style disaggregated backend in v1 or defer to v2.
- Whether to bundle a captured production trace (subject to licensing) or ship synthetic-only.
- Whether the leaderboard lives in the repo or on a hosted page.

---

*Next action: Week-1 scaffold — rename directory, freeze adapter API v0, land W1 loader.*
