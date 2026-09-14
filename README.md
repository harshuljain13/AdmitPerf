<p align="center">
  <img src="docs/assets/banner.svg" alt="AdmitPerf — benchmark-driven admission control layer for LLM inference" width="100%"/>
</p>

# AdmitPerf

*A benchmark-driven admission control layer for LLM inference.*

AdmitPerf is two things in one repository: an **admission control library** you put in front of vLLM / SGLang (it decides admit / defer / reject per request), and a **benchmark harness** that runs any policy against a real engine on real hardware and reports what it cost. Same code path in production and in the benchmark, so the numbers are trustworthy because you can also deploy them.

> **Why this exists** — Across 14 admission-primary papers in the [companion survey](../survey/), no two share a baseline, engine version, workload, or SLO definition. AdmitPerf makes head-to-head comparison possible. See [`docs/motivation.md`](docs/motivation.md).

**Status**: working MVP. Policies, the vLLM adapter, the runner, Modal provisioning, and results all exist; the Modal path has not yet been exercised against real hardware. See [`docs/status.md`](docs/status.md).

---

## The loop

```bash
admitperf infra up --model Qwen/Qwen3-0.6B --gpu A10G   # start a real vLLM on a GPU
admitperf smoke                                          # is it actually serving?
admitperf run --policy no_admission --rate 30 -n 500     # baseline
admitperf run --policy kv_threshold  --rate 30 -n 500    # challenger
admitperf infra down                                     # stop paying for it
```

`infra up` writes the endpoint to `.admitperf/session.json`; `run` reads it. They are separate commands because loading a model takes minutes and you will run many policies against one deployment. Already have an engine running somewhere? Skip provisioning and pass `--engine-url`.

Each run writes a directory you can open:

```
results/<timestamp>-<policy>/
├── manifest.json     what was run, against what, with which settings
├── summary.json      the numbers, each tagged with where it came from
├── decisions.jsonl   every admit/defer/reject, with the state it was decided on
└── outcomes.jsonl    per-request TTFT, inter-token gaps, deadline verdict
```

## Try it without a GPU

`scripts/fake_vllm.py` is a stdlib-only stand-in that speaks the three endpoints AdmitPerf touches. It gets busy under load — KV pressure rises with requests in flight and tokens slow down — so a policy has something real to react to.

```bash
python scripts/fake_vllm.py --port 8077          # terminal 1

admitperf smoke --engine-url http://127.0.0.1:8077
admitperf run --policy no_admission --engine-url http://127.0.0.1:8077 -n 60 --rate 25
admitperf run --policy kv_threshold  --engine-url http://127.0.0.1:8077 -n 60 --rate 25
```

| policy | offered | admitted | TTFT p99 | goodput |
|---|---|---|---|---|
| `no_admission` | 60 | 60 (100%) | 254ms | 1.0 |
| `kv_threshold` | 60 | 30 (50%) | 82ms | 0.5 |

Shedding half the load cuts tail latency by 3×. Note the baseline still wins on goodput: this fake fleet meets every deadline anyway, so refusing work is pure loss. That is the metric behaving correctly — **a policy cannot win by rejecting traffic**, because the denominator is everything offered rather than everything admitted.

None of those timings mean anything about real hardware. The fake exists to prove the wiring.

## Writing a policy

```python
from admitperf import AdmissionPolicy, Decision, Request, SystemState

class KVThreshold(AdmissionPolicy):
    """Reject when the KV cache is above 90% utilization."""

    name = "kv_threshold"
    requires = frozenset({"kv_used_fraction"})   # checked once, at startup

    def decide(self, req: Request, state: SystemState) -> Decision:
        if (state.kv_used_fraction or 0.0) >= 0.90:
            return Decision.reject(reason="kv_pressure")
        return Decision.admit()
```

`requires` is how a policy says which signals it needs. If the engine cannot report one, the run stops at startup with a readable error instead of silently reading the missing value as zero and behaving as an admit-everything baseline.

Policies in **your own** pip package are discovered automatically — no edit to this repo:

```toml
[project.entry-points."admitperf.policies"]
my_policy = "my_pkg.policies:MyPolicy"
```

`admitperf policies` lists everything resolvable, built-in or installed.

## Repo layout

```
admitperf/
├── src/admitperf/
│   ├── core/          the four objects, policy base + registry, state cache, runner
│   ├── engines/       vLLM adapter: scrape /metrics, run requests, time the stream
│   ├── infra/         provision a GPU and remember where it is
│   ├── bench/         load generation and results
│   └── policies/      built-in policies
├── docs/              motivation, scope, design, metrics, plan, architecture diagrams
├── scripts/           fake_vllm.py — a pretend engine for testing without a GPU
├── tests/
├── Makefile           make test · make lint · make diagrams
└── CONTRIBUTING.md    how to add a policy, conventions, PR flow
```

Architecture in [`docs/architecture/`](docs/architecture/), following the [C4 model](https://c4model.com). The `.mmd` files are the source of truth; `make diagrams` re-renders the PNGs.

## Installing

```bash
pip install -e .                 # the decision path: click, httpx, pyyaml, rich
pip install -e '.[modal]'        # + provisioning on Modal
pip install -e '.[dev]'          # + pytest, ruff, mypy
```

The runtime dependency list is deliberately short. Putting admission control in front of a fleet should not drag in a plotting stack.

### Environment

Nothing is required. The fake-engine path needs no configuration at all, and deploying an
ungated model to Modal needs only your existing `modal setup` credentials.

`.env.example` documents the optional knobs. The one that matters:

```bash
# gated weights only (Llama, Gemma) — ungated models need nothing
modal secret create huggingface HF_TOKEN=hf_xxx
admitperf infra up --model meta-llama/Llama-3.1-8B --hf-secret huggingface
```

The token lives in a Modal secret, never in `.env` — putting it there would bake it into
the deploy environment.

---

## Documentation

| Read this to... | Go here |
|---|---|
| Understand why AdmitPerf exists | [`docs/motivation.md`](docs/motivation.md) |
| See which admission policies fit behind this API | [`docs/scope.md`](docs/scope.md) |
| See the C4 architecture | [`docs/architecture/`](docs/architecture/) |
| Read the data-flow + reproducibility contract | [`docs/design.md`](docs/design.md) |
| Read the metric definitions, and what is not measurable | [`docs/metrics.md`](docs/metrics.md) |
| Read the candidate policy list + fidelity rules | [`docs/policies.md`](docs/policies.md) |
| Read the plan | [`docs/PROPOSAL.md`](docs/PROPOSAL.md) |
| Read the adversarial review of the framing | [`docs/prior-art/adversarial_review.md`](docs/prior-art/adversarial_review.md) |
| See what exists and what does not | [`docs/status.md`](docs/status.md) |
| Understand where the design came from | [`docs/lineage.md`](docs/lineage.md) |

## Contributing

New policies, workloads, engine adapters, and metrics are welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the adapter contract, the fidelity rule for reference-policy ports (never claim `"CONCUR"` when you mean `"CONCUR-inspired"`), conventions, and PR flow.

## Authors

- **Harshul Jain**
- **Dr. Tanmay Sah**
- **Tanya Sah**
- **Parv Khatri**

All independent researchers. Co-first-author ordering resolved at freeze.

## Citation

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
- Working gateway this derives from: `../../llm-inference-experiments/module7-admission-and-routing/`
- Umbrella roadmap: [`../Roadmap.md`](../Roadmap.md)

## License

MIT for code. CC-BY 4.0 for docs and results bundles.
