# Reviewer #2 Report — AdmitBench

*Auditor: Adversarial Reviewer agent, role-playing hostile Reviewer #2*
*Date: 2026-09-10*

**Recommendation: Reject (workshop) / Strong Reject (main track).** The submission conflates a systems measurement study with a benchmark contribution, and every claimed novelty has been partially or fully preempted by work published in the last 18 months.

---

## 1. Novelty Attack

### Claim 1: "First open, neutral harness for admission-policy comparison"
**Strongest prior:** Etalon (arXiv:2407.07000) already provides a holistic serving evaluation harness with pluggable schedulers. SLOs-Serve (arXiv:2504.08784) also runs head-to-head admission comparisons in its evaluation. MLPerf Inference multi-turn (July 2026) is the death blow: MLCommons is the neutral-benchmark authority, and any "first neutral" claim from a solo author two months later is untenable.

**Residual novelty: Weak.** The word "neutral" does no work when MLPerf exists.

**Reframing:** Drop "first" and "neutral." Reframe as *"an admission-policy-focused extension to Etalon, with a policy zoo not covered by MLPerf's multi-turn track."*

### Claim 2: "Faithful re-implementations of 6 policies in a common framework"
**Strongest prior:** Re-implementation is engineering, not a research contribution. Worse, "faithful" is unfalsifiable without the original authors' sign-off, and you have neither the time nor the standing to obtain it.

**Residual novelty: None.** Re-implementation is a *method*, not a *contribution*.

**Reframing:** Reposition as an *artifact release* (policy zoo repo), not a paper claim.

### Claim 3: "Head-to-head across 3 workloads × 2 model sizes"
**Strongest prior:** SLOs-Serve already does head-to-head admission comparisons. Etalon covers multiple workloads. A 3×2 grid on Qwen2.5 is a *measurement study*, not a benchmark — benchmarks are defined by community adoption, not by the authors running a matrix.

**Residual novelty: Weak.**

**Reframing:** Drop "benchmark" framing. Call it a *measurement study* and let empirical findings carry the paper.

### Claim 4: "Novel metric suite: agent-completion, preemption-loss, Jain-fairness-on-agents"
**Strongest prior:** Agent-completion is implicit in AgenticSwarmBench and in MLPerf multi-turn's per-conversation success. Preemption-loss is described in FastServe and analyzed in Chronos. Jain's fairness index dates to 1984; applying it to "agents" instead of "flows" is a relabeling.

**Residual novelty: Weak-to-None.**

**Reframing:** Drop the "novel metric" claim entirely. If there is a genuinely new metric — e.g., *preemption-loss normalized by agent-turn-position* — isolate that one and defend it hard.

---

## 2. Methodology Attack

**Is 6-policy comparison scientifically meaningful?** No, as currently framed. Six policies × three workloads × two models = 36 cells. Without a *hypothesis*, this is a table dump. The paper needs one to three headline hypotheses driving the design.

**Are re-implementations valid evidence?** No. When you re-implement FastServe and it loses to Chronos, the FastServe authors will (correctly) say "you didn't implement our KV-cache eviction policy faithfully." You have no defense. Every finding will be caveated by "as implemented in our harness." That caveat destroys the "neutral benchmark" pitch.

Mitigation demands: (a) contact each policy's authors for sign-off before submission — you will not do this in 2 weeks; (b) release code and invite response; (c) publish per-policy micro-benchmarks demonstrating fidelity to the original paper's reported numbers on the original paper's workload.

**Does the MLPerf analogy hold?** No. MLPerf works because (i) vendors self-submit, (ii) rules are ratified by a consortium, (iii) submissions are audited. AdmitBench has none of these. Calling a solo two-week measurement study "MLPerf for admission" is the kind of overclaim that annoys reviewers into rejecting on tone alone.

**The "so what" test:** After reading, what does a practitioner do differently? Current framing gives no actionable takeaway. A strong version would say: *"if your workload is >30% agentic-with-tool-calls, replace vLLM-default with Chronos; we quantify the goodput cliff."* That is a paper.

---

## 3. Venue-Fit Attack

**ICLR 2027 workshop:** ICLR reviewers are ML researchers, not systems people. Killing objection: *"this is a systems paper submitted to an ML venue."* Acceptance probability: 25% if workshop is efficient-inference-themed, 5% otherwise.

**ES-FoMo @ ICML 2027:** Best fit on topic. Killing objection: *"contribution is engineering, not scientific insight."* ES-FoMo has become competitive and prefers papers with algorithmic novelty. Acceptance probability: 30%.

**MLSys 2027 D&B track:** The right venue in principle, but D&B reviewers are harshest on the "is this actually a benchmark or a measurement study" distinction. Killing objection: *"no evidence of community adoption; overlaps with MLPerf multi-turn."* Acceptance probability: 15%.

**arXiv preprint at Day 14:** Guaranteed acceptance, but reputational risk: if re-implementations are wrong, original authors will publicly correct you. Acceptance probability: 100%; reputational EV: negative unless artifact quality is bulletproof.

**Ranking:** ES-FoMo > ICLR workshop > MLSys D&B > arXiv.

---

## 4. Ethical / Scientific Integrity Concerns

**Re-implementation disputes:** High probability that at least one of the six author groups (CONCUR, Chronos, FastServe) will publicly dispute your numbers. Mitigation is non-negotiable: pre-submission outreach with your re-implementation and reported numbers, offering right-of-reply as an appendix.

**Compute adequacy:** $1000 on 7B plus a single 72B run is *insufficient* to support "benchmark" claims. Standard practice requires (a) multiple hardware configurations, (b) multiple model families, (c) statistical significance across seeds.

**Two weeks solo:** No. Not for a "first neutral benchmark" claim. Two weeks solo is enough for: (a) an arXiv measurement study on 2–3 policies with a sharp hypothesis, or (b) an artifact release with a short technical report.

---

## 5. Steel-Man Rescue

**Verdict on current form:** Unsalvageable as pitched.

**Pivot recommendation:** Reshape to *"An Empirical Study of Admission-Control Policies Under Agentic Workloads: A Case for Workload-Aware Selection."*

**Drop:**
- "First," "neutral," "benchmark" (all three words)
- "MLPerf for admission" analogy
- Claim 2 (re-implementations as contribution)
- Claim 4 (novel metrics — unless one survives the literature check)
- The 72B run, or caveat it explicitly as a scaling anecdote

**Keep and sharpen:**
- The head-to-head measurement, but on **3 policies** (vLLM-default + 2 challengers), not 6.
- The agentic workload — this *is* where the field has a gap, and where Etalon/MLPerf are weakest.
- The policy-zoo artifact, released separately as engineering, not claimed as a research contribution.

**Add:**
- One or two falsifiable hypotheses driving the experiments.
- Fidelity micro-benchmarks: reproduce each original paper's headline number on their own workload.
- Pre-submission author outreach with right-of-reply appendix.
- Explicit positioning against Etalon and MLPerf multi-turn in the first two paragraphs.

**Realistic venue:** arXiv preprint + ES-FoMo workshop submission with the reframed pitch.

**EB1A relevance check:** A solo two-week measurement study, even accepted at a workshop, is a marginal contribution to the "scholarly articles" criterion. If the two weeks are being spent to *ship AdmitBench-the-paper*, the opportunity cost against your cross-paradigm health-voice-agent AAAI 2027 workshop track is high. If the two weeks are being spent to *build the policy-zoo artifact* that later enables a stronger paper — that reframe is EB1A-defensible.

---

**Final score:** Novelty 2/10, Methodology 3/10, Presentation-of-claims 2/10, Artifact potential 6/10, Venue fit (as pitched) 2/10.
