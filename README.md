<p align="center">
  <img src="docs/assets/banner.svg" alt="AdmitPerf — admission control benchmark for LLM inference serving" width="100%"/>
</p>

# AdmitPerf

*A reproducible harness for admission control in LLM inference serving.*
*Status: proposal accepted 2026-09-11. Week-1 scaffold in progress.*

## What it is

AdmitPerf is a pluggable-policy benchmark harness that measures **goodput-under-admission** for LLM serving systems across SLO-diverse, multi-tenant, and agent-session workloads — the axis that LLMPerf, GenAI-Perf, GuideLLM, and MLPerf Inference LLM do not cover.

Companion to the systematic-review survey at `../survey/`, which documents (a) 9-for-9 no ingress admission across top-venue LLM serving papers, (b) zero baseline overlap across 14 admission-primary papers, and (c) the empty intersection `agent-level × online × deadline-aware × fair`.

## Read this first

- **Full proposal**: `docs/PROPOSAL.md` — goals, non-goals, architecture, workload suite, reference policies, metrics contract, author split, 8-week milestones, compute plan, venue targets.
- **Design notes**: `docs/design.md`, `docs/policies.md`, `docs/metrics.md`.
- **Prior-art dossier**: `docs/prior-art/`.

## Layout

```
admitperf/
├── README.md
├── LICENSE (MIT)
├── pyproject.toml
├── .gitignore
├── .python-version
├── src/admitperf/        # policy ABCs, harness, metrics, traces, CLI
├── docs/
│   ├── PROPOSAL.md            # v0.1 proposal (2026-09-11)
│   ├── design.md
│   ├── policies.md
│   ├── metrics.md
│   └── prior-art/             # adversarial_review, feasibility_audit, gap_matrix
├── experiments/           # configs, notebooks, fidelity, results
├── data/                  # gitignored
├── tests/
└── scripts/
```

## Authors

- Harshul Jain — systems
- Dr. Tanmay Sah — theory + reference-policy port
- Tanya Sah — workloads + measurement + writing

All independent researchers. Co-first-author ordering resolved at freeze.

## Related

- Umbrella roadmap: `../Roadmap.md`
- Companion survey: `../survey/`
- Prior-art audit dossier: `../private/prior-art/`

## License

MIT for code. CC-BY 4.0 for docs and results bundles.
