# AdmitBench Prior-Art Due Diligence

*Auditor: Prior Art Deep Auditor agent*
*Date: 2026-09-10*

## 1. Gap matrix

| Work | What it measures | Admission-policy comparison? | Open harness (plug-in policies)? | Agent-aware? | Third-party neutral? | Overlap | Scoop-risk timeline |
|---|---|---|---|---|---|---|---|
| Etalon (2407.07000) | TTFT/TBT/TPOT + fluidity-index for user-experience | No | Yes (measurement layer, engine-agnostic) | No | Yes (academic) | **Med** | Extension paper plausible in 6–9 mo |
| AgenticSwarmBench (GH) | TTFT / throughput / ITL on Cursor/Claude-Code replay traces, 6K–400K ctx | No | Yes (endpoint-agnostic; OpenAI-compatible) | Yes | Yes (community) | **High** | Active repo; could add scheduler axis in a release |
| MLPerf Inference multi-turn (MLCommons) | Standardized offline/server/interactive scenarios; multi-turn added 2026 | No (fixed load generator, not admission comparison) | Partially (LoadGen is fixed spec) | Emerging (multi-turn) | Yes (consortium) | **Med** | Governance-heavy; new tasks land in ~annual cycles |
| Bench360 (2511.16682) | Local inference: latency, throughput, energy, quality × 3 GPUs × 4 engines | No | Yes | No | Yes | Low | Local-focus; unlikely to pivot to multi-tenant admission |
| LLM-Inference-Bench (GH) | Hardware sweep: NVIDIA/AMD/Habana/SambaNova throughput/latency | No | Partially | No | Yes | Low | Hardware-vendor lens; unlikely pivot |
| Harness-Bench (2605.27922) | Agent capability under different execution harnesses (context/tools/state) — 106 tasks | No (evaluates model×harness quality, not serving decisions) | Yes | Yes | Yes | Low | Different axis (quality not serving) |
| Harness-MU (2606.21856) | Multi-user agent governance / access-control (Muses-Bench) | No (access control, not queue admission) | N/A | Yes | Yes | Low | Security framing, not serving |
| InferenceBench (2607.20468) | Meta: can an agent optimize an OpenAI-compatible server in 2h/H100 | No (agent-as-optimizer) | Partial (OpenAI-compat) | Meta only | Yes | Low | Orthogonal |
| SLOs-Serve (2504.08784) | Serving system w/ multi-SLO DP allocator | Head-to-head vs prior *systems*, not admission policies isolated | No (system, not harness) | No | System paper | **Med** | Follow-on could bundle a harness |
| NexusSched (2509.23384) | Predictive two-layer scheduler (LENS + PRISM) w/ perf model | Compares vs schedulers; deployed on FlowGPT | No | No | Prod-partner | **Med** | Industrial team; unlikely to open a neutral harness |
| Clairvoyant (2606.07248) | Predictive SJF admission ordering (drop-in proxy) | Its own vs FIFO on Ollama/llama.cpp | Partial (sidecar proxy) | No (single-user) | Yes | Low | Edge-focused |
| CONCUR (2601.22705) | Agent-level admission via KV-congestion feedback | Its own policy vs status-quo | No (policy paper) | Yes | Yes | Low (candidate baseline) | Follow-on could publish a comparison — watch |
| Fluid-WAIT (2504.11320) | Threshold-based admission (WAIT / Nested-WAIT) | Its own vs baselines on Llama-2-7B sim | Simulator only | No | Yes | Low | Theory-heavy; harness follow-on possible |
| Aqua (2407.21255) | Cheap-preemption alternative to admission-based memory mgmt | Vs SOTA on responsiveness/throughput | No | No | Yes | Low | System paper |
| Service-Induced Congestion (2606.15555) | Dynamical model of admission+eviction stability | Analytic; heterogeneity as stabilizer | No | No | Yes | Low | Modeling paper |
| Flow-controlled scheduling (2604.11001) | Rate-controlling prompt intake; provable stability | Vs "commonly used strategies" on throughput/latency/KV | Sim harness (paper-internal) | No | Yes | Low | Theory; may release code |
| FairBatching (2510.14392) | Fair prefill/decode batch capacity split | Own vs decode-priority baselines | No | No | Yes | Low | Batch-formation, not admission |
| Chronos (fcomp 2026.1873627) | Formal WCRT bounds; sound TTFT/TBT admission control | Own vs uncontrolled + baseline schedulers | No | No | Yes | **Med** | RT-theory framing; a benchmark spin-off is natural |
| FastServe (NSDI 2026) | Preemptive scheduling for LLM serving | Unverified via WebFetch | Unverified | Unverified | Academic | Med (assumed) | Unverified |
| SOLA (MLSys 2025) | SLO-aware serving | Unverified via WebFetch | Unverified | Unverified | Academic | Med (assumed) | Unverified |
| ProServe (2512.12928) | Multi-priority scheduler (SlideBatching + GoRouting) | Own vs prior schedulers; +52% SLO attainment | No | No | Academic | Low-Med | System paper |
| llm-d (GH, v0.7 as of 2026) | Production K8s serving stack (routing, KV tiering, disagg, flow control) | Ships flow control but not a comparison harness | Yes for policies (pluggable) but no benchmarking harness advertised | Partial | Vendor-neutral CNCF-style | **High** | 127 open issues, 102 PRs; "flow control" is on the release train — a plug-in policy plugin + eval is highly plausible in 3–6 mo |

## 2. Scoop-risk assessment (Med/High overlap only)

**AgenticSwarmBench (High).** Same substrate: OpenAI-compatible endpoints, agentic traces that defeat prefix cache, TTFT/ITL metrics. To become a direct competitor it must (i) add a policy plug-in interface (admission hooks), (ii) implement ≥2 published admission policies, (iii) add fairness/preemption-loss metrics, (iv) add multi-tenant traces. No roadmap or issue signaling this; scope has stayed on client-side replay measurement. Risk vector: someone forks it and adds an admission axis before AdmitBench ships.

**llm-d (High).** Already offers pluggable "flow control" and multi-tenant routing on OpenAI-compatible engines; a neutral third-party could sit atop it. To compete it must (i) publish a *comparison* study across policies with a reproducible trace corpus, (ii) commit to third-party neutrality (currently a vendor-friendly consortium), (iii) surface admission as a first-class experiment axis, not a config knob. Predicted-latency scheduling and flow-control land in v0.4–0.7; a "benchmark policies inside llm-d" blog or paper is a natural next step. Mitigation: cite llm-d as a supported backend, not a competitor.

**Etalon (Med).** Owns the fluidity-index framing for user experience — overlaps AdmitBench's tail-latency/goodput story. Would need admission-specific metrics + agent workloads to compete. No public roadmap surfaced; measurement-framework framing keeps it upstream of policy comparison. Cite as the metrics ancestor.

**MLPerf multi-turn (Med).** 2026 multi-turn addition targets agentic workloads. MLPerf would need a new "admission-policy" task category — inconsistent with its "measure what a submitter builds end-to-end" philosophy, which does not isolate admission. Governance timelines are long; unlikely to scoop in 12 mo. Position AdmitBench as the sub-component study MLPerf explicitly does not do.

**SLOs-Serve, NexusSched, Chronos (Med).** All are policy/system papers doing their own head-to-head. Would need to release a neutral harness with third-party policies plugged in. Low practical risk — teams typically don't do this. Include as baseline plug-ins.

## 3. Sharpened positioning

**Pitch:** *AdmitBench is the first open, engine-agnostic harness that isolates the **admission decision** as an experimental axis — plugging published policies into a common vLLM/SGLang/TRT-LLM backend and reporting goodput, TTFT/TBT tails, agent-completion rate, preemption-loss ratio, and per-tenant fairness on a shared multi-tenant agentic trace corpus.*

**Defensible claims.**
- First harness whose *unit of comparison* is the admission policy, not the serving system.
- First to combine agent-aware traces (AgenticSwarmBench-style) with multi-tenant fairness and preemption-loss metrics.
- Third-party neutrality vs vendor-adjacent llm-d.
- Per-tenant fairness + preemption-loss ratio as reported metrics — not in Etalon, Bench360, MLPerf, or LLM-Inference-Bench.

**NOT defensible.**
- "First LLM serving benchmark" — Etalon, Bench360, MLPerf, LLM-Inference-Bench predate.
- "First agent-aware inference benchmark" — AgenticSwarmBench and MLPerf multi-turn (2026) predate.
- "First to measure TTFT/TBT under SLO" — Chronos + SLOs-Serve + NexusSched already report these.
- "New scheduling algorithm" — AdmitBench is a harness; any algorithmic claim would collide with baselines.
- "Production-grade admission control" — llm-d owns that framing.

**Unverified via WebFetch (verify manually before submission):** FastServe (NSDI 2026), SOLA (MLSys 2025).
