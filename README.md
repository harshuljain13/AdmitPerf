<p align="center">
  <img src="docs/assets/banner.svg" alt="AdmitPerf — benchmark-driven admission control layer for LLM inference" width="100%"/>
</p>

# AdmitPerf

*A benchmark-driven admission control layer for LLM inference.*

AdmitPerf is two things in one repository: an **admission control library** you drop in front of vLLM / SGLang / TRT-LLM (decides admit / defer / reject per request), and a **benchmark harness** that measures every policy the library ships against a common workload and scoreboard. Same code path in production and in the benchmark, so the numbers are trustworthy because you can also deploy them.

> **Why this exists** — Across 14 admission-primary papers in the [companion survey](../survey/), no two share a baseline, engine version, workload, or SLO definition. AdmitPerf makes head-to-head comparison possible. See [`docs/motivation.md`](docs/motivation.md).

**Status**: scaffold. Adapter API v0 is frozen; runtime and benchmark harness are empty. See [`docs/status.md`](docs/status.md).

---

## Quickstart

```bash
pip install admitperf                    # coming soon — not yet published
```

Write a policy in a dozen lines:

```python
from admitperf import AdmissionPolicy, Decision, Request, SystemState

class KVThreshold(AdmissionPolicy):
    """Reject when KV cache is above 90% utilization."""
    name = "kv_threshold"

    def decide(self, req: Request, state: SystemState) -> Decision:
        if (state.kv_used_fraction or 0.0) > 0.90:
            return Decision.reject(reason="kv_pressure")
        return Decision.admit()
```

Run the benchmark against your policy (once the harness lands in Week 1):

```bash
admitperf run --policy kv_threshold --workload synthetic-poisson --duration 60s
```

---

## Repo layout

```
admitperf/
├── src/admitperf/         Python package (policies, harness, metrics, traces, CLI)
├── docs/                  motivation, scope, design, metrics, plan, architecture diagrams
├── experiments/           configs, notebooks, results, fidelity notes
├── tests/                 unit + smoke tests
├── scripts/
├── data/                  (gitignored — traces and run outputs)
├── Makefile               make diagrams · make test · make lint
└── CONTRIBUTING.md        how to add a policy, coding conventions, PR flow
```

Full architecture in [`docs/architecture/`](docs/architecture/) — 5 mermaid diagrams following the [C4 model](https://c4model.com): context, container, component, dynamic, plus an 8-week plan companion. The `.mmd` files are the source of truth; run `make diagrams` to re-render the PNGs after editing one.

---

## Documentation

| Read this to... | Go here |
|---|---|
| Understand why AdmitPerf exists | [`docs/motivation.md`](docs/motivation.md) |
| See which admission policies fit behind this API | [`docs/scope.md`](docs/scope.md) |
| See the C4 architecture (context → container → component → dynamic) | [`docs/architecture/`](docs/architecture/) |
| Read the data-flow + reproducibility contract | [`docs/design.md`](docs/design.md) |
| Read the metric definitions | [`docs/metrics.md`](docs/metrics.md) |
| Read the candidate policy list + fidelity rules | [`docs/policies.md`](docs/policies.md) |
| Read the 8-week plan | [`docs/PROPOSAL.md`](docs/PROPOSAL.md) |
| Read the adversarial review of the framing | [`docs/prior-art/adversarial_review.md`](docs/prior-art/adversarial_review.md) |
| See what already exists and what's empty | [`docs/status.md`](docs/status.md) |
| Understand the lineage from module6 gateway | [`docs/lineage.md`](docs/lineage.md) |

---

## Contributing

New policies, workloads, engine adapters, and metric contributions are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for:

- adapter API contract every policy must implement
- coding conventions (ruff, pytest, type hints)
- fidelity rules for reference-policy ports (never claim `"CONCUR"` when you mean `"CONCUR-inspired"`)
- PR flow and commit-message style
- how to run the smoke test locally

---

## Authors

- **Harshul Jain**
- **Dr. Tanmay Sah**
- **Tanya Sah**
- **Parv Khatri**

All independent researchers. Co-first-author ordering resolved at freeze.

## Citation

If you use AdmitPerf, please cite the companion survey (arXiv preprint pending) and this repository:

```bibtex
@software{admitperf2026,
  title = {AdmitPerf: A Benchmark-Driven Admission Control Layer for LLM Inference},
  author = {Jain, Harshul and Sah, Tanmay and Sah, Tanya and Khatri, Parv},
  year = {2026},
  url = {https://github.com/harshuljain13/AdmitPerf}
}
```

## Related

- Companion survey: [`../survey/`](../survey/)
- Working gateway this derives from: `../../llm-inference-experiments/module6-admission-and-routing/`
- Umbrella roadmap: [`../Roadmap.md`](../Roadmap.md)

## License

MIT for code. CC-BY 4.0 for docs and results bundles.
