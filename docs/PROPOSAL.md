# AdmitPerf — Project plan

*Proposal v0.5 — 2026-09-14*
*Authors: Harshul Jain, Dr. Tanmay Sah, Tanya Sah, Parv Khatri (all independent researchers)*
*Companion to a systematic review of the admission-control literature (preprint pending).*

This document owns the **plan**: goals, workloads, milestones, compute, venues, risks.

It does not restate anything that another document owns:

| For... | See |
|---|---|
| Why this project exists, and the survey findings behind it | [`motivation.md`](motivation.md) |
| What is in and out of scope, Class A vs Class B policies | [`scope.md`](scope.md) |
| Architecture | [`architecture/`](architecture/) |
| Data-flow, purity, and reproducibility contracts | [`design.md`](design.md) |
| Metric definitions | [`metrics.md`](metrics.md) |
| The concrete policy roster and fidelity rules | [`policies.md`](policies.md) |
| What exists in the repo today | [`status.md`](status.md) |

---

## 1. Goals

- **G1** — A policy-adapter API that lets researchers plug an admission policy into vLLM and
  SGLang without engine-specific glue code. *(Frozen as of 2026-09-11.)*
- **G2** — A workload suite covering request-level SLO mixes, multi-tenant fairness, and
  agent-session traces (§2).
- **G3** — A metric contract centered on **goodput-under-admission**, with reject/defer
  accounting as first-class citizens. Defined in [`metrics.md`](metrics.md).
- **G4** — Reference implementations for the policy roster in [`policies.md`](policies.md),
  every one of them Class A per [`scope.md`](scope.md).
- **G5** — Reproduction from one file: `admitperf bench run -c <config.yaml>` produces a
  results bundle recording the resolved config, so a number can be traced to the engine
  settings that produced it.

## 2. Workload suite

- **W1 request-mix** — Poisson arrivals over three SLO classes (interactive 500 ms TTFT /
  streaming 2 s TTFT / batch 30 s completion), ShareGPT + LMSYS-Chat-1M prompts.
- **W2 tenant-fairness** — N tenants with heterogeneous rate and priority, plus adversarial
  burst scenarios.
- **W3 agent-session** — multi-turn conversations with tool-call latencies stitched in,
  long-tail context growth.
- **W4 reasoning-heavy** — mixed decode-length workload with hidden reasoning traces
  (survey Open Problem 4).

All workloads ship as JSONL with a schema and validator; synthetic generators plus captured
traces where licensing permits.

## 3. Milestones (8 weeks)

Rendered as [`architecture/05-plan.png`](architecture/05-plan.png).

| Week | Milestone |
|---|---|
| 1 | ✅ vLLM adapter, runner, W1 loader, results bundle, Modal provisioning, CLI |
| 2 | ✅ First run on real hardware; queue-depth policies; config + CLI split |
| 3 | Chronos-inspired threshold WCRT; report artifact |
| 4 | QLM-inspired KV admission + metric formalization |
| 5 | W3 agent-session workload, token-budget + CONCUR-inspired agent-level admission |
| 6 | Full sweep (size set by the open question in §7) |
| 7 | Cross-policy leaderboard, results bundle v1 |
| 8 | Paper draft freeze, arXiv preprint |

Week 1 detail and definition-of-done live in [`status.md`](status.md).

## 4. Compute plan

- **Dev** — 1×A10G on Modal, ~$0.60/hr, 60 hrs/wk cap. ~$150/wk during weeks 1–5.
- **Sweep** — 4×A100 or 2×H100 on Modal or Lambda for weeks 6–7, ~$800–1200 total.
- **Total budget target** — under $2500.
- **Fallback** — 1×A10G with smaller models (Llama-3-8B, Qwen3-7B) if the H100 budget slips.

## 5. Venue targets

| Venue | Deadline | Fit | Notes |
|---|---|---|---|
| MLSys 2027 D&B | 2026-10-30 | high | tight (7 weeks); stretch |
| NeurIPS 2027 D&B | 2027-06 | high | comfortable |
| ES-FoMo @ ICML 2027 | ~2027-02 | high | workshop venue for community feedback |
| arXiv preprint | week 8 | required | ships with code + leaderboard |

Primary target: **ES-FoMo @ ICML 2027** for community adoption, plus **NeurIPS 2027 D&B**
for archival credit. MLSys 2027 D&B only if the week-6 sweep lands clean.

## 6. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Engine API drift (vLLM/SGLang) | high | pin versions in manifest; adapter shim isolates changes |
| Policy port inaccuracy | medium | fidelity contract in [`policies.md`](policies.md) |
| Compute overrun | medium | 8B-model fallback path; Modal spot instances |
| Name collision (post-launch) | low | AdmitPerf verified clean on GitHub + Google (2026-09-11) |
| Reviewer critique: "just a benchmark" | medium | leaderboard + survey pairing shows the community wedge; see [`prior-art/adversarial_review.md`](prior-art/adversarial_review.md) |

## 7. Open questions

Unresolved. Each one changes the build, so none should be answered by accident.

1. ~~**Execution substrate.**~~ **Resolved 2026-09-14: live engines only.** A simulated
   engine cannot answer whether one policy beats another on a real serving stack, which is
   the question the project exists to ask. The simulator built earlier was removed. What
   remains of the "cheap and deterministic" corollary in [`motivation.md`](motivation.md)
   is a tension worth naming: a run against real hardware is not bit-reproducible, so
   reproducibility here means a pinned manifest and a published bundle, not identical
   numbers. `scripts/fake_vllm.py` covers the wiring; it is explicitly not a measurement
   substrate.
2. **Policy count.** The roster in [`policies.md`](policies.md) versus the 3–4 that
   [`prior-art/feasibility_audit.md`](prior-art/feasibility_audit.md) argues is achievable.
3. **Driving hypothesis.** Whether the work is organized around one falsifiable question
   (*does request-level admission match agent-level admission on agentic workloads?* — a
   public open question from the CONCUR ICML review) or around a policy × workload matrix.
4. **Output form.** Benchmark, measurement study, or artifact release. The adversarial
   review argues hard against "benchmark."
5. Whether to bundle a captured production trace (licensing) or ship synthetic-only.
6. Whether the leaderboard lives in the repo or on a hosted page.
7. ~~How many repeats per configuration.~~ **Partly resolved**: repeats are first-class and
   `bench compare` reports the observed range. Still open is how many are enough — two
   showed the baseline's spread at ±725ms against ±136ms for a shedding policy, so the
   answer is workload-dependent rather than a constant.
8. Which regime to test next. The first run was concurrency-bound, where KV pressure is a
   flat line; the KV-bound regime needs a larger model with long contexts, and until that
   runs there is no evidence about which signal is generally better.
