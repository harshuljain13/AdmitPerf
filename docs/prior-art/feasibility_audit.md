# AdmitBench Feasibility & Fidelity Audit

*Auditor: Feasibility & Fidelity Critic agent*
*Date: 2026-09-10*

**Verdict up front:** A publishable-quality preprint at Day 14 with 6 policies × 3 workloads × 2 models is **not feasible**. A defensible v0.1 preprint with 3 policies × 2 workloads × 1 model (7B) is tight-but-doable. The 72B run and half the policy list should be cut on Day 1, not Day 10.

## 1. Per-policy fidelity risk

| Policy | Public code? | Language | Engine hooks needed | LoC estimate | Fidelity risk | Notes |
|---|---|---|---|---|---|---|
| vLLM-default | Yes (vLLM v1 `Scheduler`) | Python | None | ~0 | Low | Reference baseline. Just leave the default `SchedulerConfig`. |
| CONCUR (arXiv:2601.22705) | **No public code found** | N/A | Agent-level middleware; needs per-agent tagging + KV-pressure signal from engine `/metrics` or scheduler introspection | ~600–1000 | **High** | Paper specifies AIMD but hyperparameters (α, β, MD factor, RTT-analog window) are non-obvious. Baseline in the paper is SGLang+HiCache, not vLLM — porting the pressure signal to vLLM requires reading `KVCacheManager` free-block counters. Author blog exists but no code. Any re-implementation will be an *interpretation*, not a reproduction. |
| Fluid-WAIT (arXiv:2504.11320) | Evaluated in Vidur simulator; **no released harness** | N/A (simulator) | Threshold admission fits cleanly *outside* engine; needs known/estimated output length | ~300–500 | **Med** | The WAIT threshold is derived from a fluid model with per-workload parameters (M*, equilibrium batch). You must re-solve the fluid model per trace or use Nested WAIT (unknown lengths) with a "safety buffer of moderate scale" — the paper does not pin this constant. Expect a 1–2 day tuning phase per workload. |
| FairBatching (arXiv:2510.14392) | **No repo surfaced** | N/A | Deep — modifies batch *formation* (prefill/decode interleaving), not just admission. This is a scheduler replacement, not an outer layer. | ~800–1200 | **High** | It's an enhanced-EDF policy inside the batching loop. In vLLM this means subclassing `vllm.v1.core.sched.scheduler.Scheduler` and rewriting `schedule()`. Sarathi-style chunked-prefill interaction is a known landmine. |
| Chronos (Frontiers, 10.3389/fcomp.2026.1873627) | **No code** (theory paper) | N/A | Admission only (WCRT-based) — outer layer OK | ~400–600 | Med | Sporadic-task model + response-time analysis is textbook. Main risk: getting worst-case prefill/decode-iteration bounds calibrated per model. Paper gives numbers for its own harness, not vLLM. |
| FastServe (NSDI '26) | **Yes** — [LLMServe/FastServe](https://github.com/LLMServe/FastServe), [NSDI26-FastServe mirror](https://github.com/MachineLearningSystem/NSDI26-FastServe) | Python + C++ (SwiftTransformer, *not* vLLM) | Skip-join MLFQ + iteration-level preemption + proactive KV offload — requires token-level preemption hook | ~1500+ to port to vLLM | **High** | The published system does not run on vLLM/SGLang. Their preemption model depends on SwiftTransformer internals. Re-implementing skip-join MLFQ as a vLLM scheduler subclass is feasible; faithfully reproducing the proactive KV offload is not, in 2 weeks. |

**Total re-implementation budget: ~3600–5300 LoC of carefully tuned scheduling code, of which ~60% touches vLLM internals.** With one solo engineer + AI assistant, a realistic sustainable rate is ~400–600 net LoC/day of *correct, tested* scheduler code. That's 6–10 engineering days for policies alone — before any benchmarking.

## 2. Trace corpus availability

| Source | Public? | Has needed fields? | Effort | Rating |
|---|---|---|---|---|
| Azure LLM Inference Dataset 2023/2024/2025 | Yes, CC-BY | TIMESTAMP, ContextTokens, GeneratedTokens ✓. **No tenant IDs, no agent-turn boundaries** ✗ | ~0.5 day load-in | **Ready** for request-level; useless for agent metrics |
| LMSYS-Chat-1M | Gated, license click-through | `conversation_id`, turns, model ✓. **No timestamps, no pre-computed tokens** ✗ | 1–2 days: tokenize with tiktoken/HF, synthesize arrival times (Poisson replay) | **Needs adaptation** |
| SWE-bench trajectories | Yes — SWE-smith-trajectories (5017 traj), plus cross-framework corpus (64k traj) | Tool-call boundaries ✓, tokens computable, no arrival times | 2–3 days to normalize to a common schema | **Needs adaptation** |
| τ-bench | Framework is public (Sierra); **trajectories not centrally released as a trace corpus** | Must run τ-bench yourself to generate traces, or use τ²-bench slice from the cx-cmu release (gated) | 3–5 days if generating | **Needs synthetic augmentation** |
| GAIA | Benchmark public; traces via TRAIL (148 expert-annotated) or Open Deep-Research | Small volume; not enough for statistical claims on its own | 1–2 days | **Needs adaptation** |
| ToolBench | Repo (ToolLLM/ToolBench) has training trajectories; **no confirmed release surfaced in search** | Uncertain schema for arrival/turn timing | 2–4 days investigation + adaptation | **Needs adaptation** (schedule risk) |
| Synthetic agentic generator | To be built | You define the schema | 1–2 days | **Ready-with-build** |

**Reality:** There is exactly *one* trace corpus (Azure) that is drop-in ready, and it has none of the agent structure that motivates the whole benchmark. Everything agent-flavored requires normalization to a common schema — a **3–5 day sub-project** by itself.

## 3. Metric measurement feasibility

| Metric | Measurable externally? | If not, where to modify |
|---|---|---|
| TTFT, TBT | Yes | vLLM emits both via `/metrics` (Prometheus). Ready. |
| Throughput, goodput | Yes | Client-side timing + SLO threshold. Ready. |
| Agent-completion rate | Yes | Client-side: define "agent" as a sequence of correlated request IDs; count fully-completed sequences. Requires you own the trace replayer. Ready. |
| Preemption-loss ratio (KV bytes discarded / generated) | **Partially — requires engine mod** | vLLM exposes `vllm:num_preemptions_total` but **not KV bytes evicted**. To get bytes, instrument `vllm/v1/core/kv_cache_manager.py` (block free path) and `vllm/v1/core/sched/scheduler.py` (preemption path). ~50 LoC patch. |
| Jain-fairness-on-agents | Yes | Computed from per-agent goodput. Ready. |
| Admission-decision histogram | Yes | Your admission layer owns the decision — log it. Ready. |
| KV pressure over time | Yes | vLLM exposes `vllm:gpu_cache_usage_perc` in Prometheus. Ready, but polling at scheduler-iteration granularity (~10ms) needs `/metrics` scrape at ≥100Hz or in-process callback. |

**Verdict:** One engine patch (preemption-loss bytes) is unavoidable. Everything else lives outside the engine. This is the *least* risky part of the plan.

## 4. Compute wall-clock reality check

**Modal H100 effective rate: ~$3.95–$4.50/hr.** Use $4.25/hr as planning number.

**Per-config sweep time for statistical significance:**
- Warmup: 5 min
- Steady-state measurement: need ≥1000 completed requests for stable P99, at ~2–5 req/s sustained on 7B = ~5–10 min
- 3 seeds for CI = 3×
- **Minimum ~45 min/config on 7B (1× H100), ~2 hr/config on 72B (4× H100)**

**Matrix as stated: 6 × 3 × 2 = 36 configs.**
- 7B configs (18): 18 × 0.75 hr × 1 GPU = **13.5 H100-hr → $57**
- 72B configs (18): 18 × 2 hr × 4 GPUs = **144 H100-hr → $612**

That looks fine on paper. It is not. Missing from the budget:
- **Debugging/rerun overhead: 3–5×** typical for first-time scheduler benchmarking. → +$2000
- **Failed runs from OOMs during policy tuning** (especially CONCUR/FairBatching where hyperparameters aren't given). → +$500–1000
- **72B warmup + loading**: model download + weight load ~15 min per cold container. On serverless, cold starts destroy the budget for short runs. → +$300
- **Trace-processing sanity runs on CPU-heavy Modal containers** — cheap but non-zero.

**Realistic total: $2500–4000.** The $1000 budget is **under-budgeted by 2.5–4×** for the stated matrix. It's realistic *only if you kill the 72B run entirely* and cut the config matrix by ≥50%.

## 5. Kill-list — cuts required for Day-14 preprint

Ranked, cut top-to-bottom as timeline slips:

1. **Kill the 72B headline run.** No public code for 3 of 6 policies means you'd be debugging OOMs at 72B with unvetted implementations. Report on 7B only; footnote 72B as future work. Saves ~$600 + 3–4 days of tuning.
2. **Drop CONCUR *or* FastServe — not both.** Both are high fidelity risk. CONCUR has no code and depends on a signal (aggregated agent KV pressure) that vLLM doesn't natively expose. FastServe has code but on a different engine. Pick the one that fits your story better; re-implement the other as "future work — invited PR."
3. **Drop FairBatching.** Deep scheduler rewrite with no released code. Highest LoC estimate, highest risk of subtle wrongness.
4. **Cut τ-bench and ToolBench traces from v0.1.** Neither has a clean trace corpus. Ship with Azure (request-level) + LMSYS (conversation-level replay) + SWE-smith-trajectories (agent) + your synthetic generator.
5. **Cut agent-completion metric down to "any-turn-succeeds"** instead of full-trajectory success. Full trajectory scoring requires re-executing task environments (SWE-bench Docker, τ-bench simulator). That's a week alone.
6. **Cut Jain-fairness across tenants**, keep it across agents. Tenants require synthetic multi-tenant tagging that Azure trace doesn't have.
7. **If Day 10 arrives and CONCUR isn't converging:** ship 3 policies (vLLM-default, Chronos, FastServe-port or Fluid-WAIT), 2 workloads (Azure + SWE-smith), 1 model (Qwen2.5-7B). This is still a legitimate preprint: "AdmitBench v0.1: a reproducible harness for LLM admission control, with three reference policies."

## Bottom line

The 2-week plan as written is doing three hard things in parallel (build a harness, re-implement 6 policies from scratch, produce headline numbers on a 72B model). Any one of those is a 2-week job. Pick the harness + 3 policies + 7B; ship on Day 14. The 72B run and the missing three policies become v0.2 and the paper's headline "call to action."
