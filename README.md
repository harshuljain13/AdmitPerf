# AdmitBench (deferred — Track 2)

**Status**: paused as of 2026-09-10.

## Why paused

After 3-researcher critical audit + 5 rounds of prior-art re-verification, the field turned out more crowded than initial scoping assumed. Peer-reviewed admission-control papers exist at ICML 2026 (CONCUR, Shen et al.), SoCC '24 (QLM), MLSys 2025 (SOLA), NSDI 2026 (FastServe), Frontiers CS 2026 (Chronos), plus adjacent work at ICML 2024/2025 (InferCept, Cake), NeurIPS 2025 (KVFlow), SOSP 2025 (Pie).

A 2-week solo benchmark paper into this crowded space would produce marginal contribution. Instead, primary effort redirected to a **survey** (Track 1) that establishes taxonomy and reference position. AdmitBench resumes as Track 2 after survey ships.

## Original intent (preserved for future resumption)

An open benchmark harness that plugs published LLM admission-control policies against a common trace corpus on OpenAI-compatible engines (vLLM/SGLang/TensorRT-LLM), reports goodput, tail latency, agent-completion rate, preemption-loss ratio, and per-tenant fairness. Position on the admission-decision axis (not agentic inference speed, not general serving throughput).

## What will change when resumed

The survey (Track 1) will document the specific open question worth answering empirically. Current best candidate — from CONCUR ICML 2026 Reviewer Afcg's public comment: *"the underlying problem can be addressed through more effective request-level scheduling."* AdmitBench v2 answers this by cross-testing request-level admission (QLM, Chronos-inspired, Fluid-WAIT) against agent-level (CONCUR-inspired) on CONCUR's own BrowserComp setup.

## Layout (existing scaffold)

```
admitbench/
├── README.md            # this file
├── LICENSE (MIT)
├── pyproject.toml
├── .gitignore
├── .python-version
├── src/admitbench/      # ABCs for Policy, TraceLoader, SystemState — usable when we resume
├── docs/                # design.md, policies.md, metrics.md
├── experiments/         # empty; will fill on resume
├── data/                # gitignored
├── tests/               # smoke tests
└── scripts/
```

## Related

- Umbrella roadmap: `../Roadmap.md`
- Active survey work: `../survey/`
- Prior-art audit dossier: `../private/prior-art/`
