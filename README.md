<p align="center">
  <img src="docs/assets/banner.svg" alt="AdmitPerf — benchmark-driven admission control layer for LLM inference" width="100%"/>
</p>

# AdmitPerf

*A benchmark-driven admission control layer for LLM inference.*

Two things in one repository: an **admission control library** you put in front
of vLLM / SGLang — it decides admit / defer / reject per request — and a
**benchmark harness** that runs any policy against a real engine on real
hardware and reports what it cost. Same code path in both, so a number you can
defend is also a thing you can deploy.

> **Why** — across 14 admission-primary papers surveyed, no two share a
> baseline, engine version, workload, or SLO definition. Head-to-head
> comparison is not currently possible. [`docs/motivation.md`](docs/motivation.md)

**Status**: working MVP, verified on real hardware.
[Results](docs/results.md) · [honest gaps](docs/status.md)

## Start here

No GPU, nothing to pay for:

```bash
pip install -e '.[all]'
make mock-engine        # terminal 1: a mock engine that gets busy under load
make dashboard          # terminal 2: configure an experiment, run it, read it
```

The dashboard runs the whole pipeline — provision, run, aggregate, report, tear
down — one stage at a time. [`dashboard/README.md`](dashboard/README.md)

## Or from the CLI

Two command groups, matching the two jobs: `infra` provisions, `bench` measures.

```bash
admitperf infra up   -c experiments/shedding-vs-tail-latency.yaml   # real vLLM on a real GPU
admitperf infra smoke                           # is it actually serving?
admitperf bench run  -c experiments/shedding-vs-tail-latency.yaml   # every policy, repeated
admitperf bench compare results/                # who won, and by how much
admitperf bench report  results/                # report.html you can attach
admitperf infra down                            # stop paying for it
```

Every policy faces the **same deployment** — started once, never re-provisioned
between policies — so the accept/refuse decision is the only thing that varies.
`infra up` writes the endpoint to `.admitperf/session.json` and `bench run`
reads it; point at your own engine instead with `--engine-url`.

To vary the hardware or engine settings too, add a `matrix:` and use
`bench sweep`. Results stay grouped per deployment and are never pooled: a table
mixing an A10G row with an A100 row reports the machine, not the policy.

Everything lives in one config file, and flags override it for one-offs.
Examples in [`experiments/`](experiments/).

## What a run leaves behind

```
results/<timestamp>-<experiment>/<policy>-r<n>/
├── manifest.json     what was run, against what, with which settings
├── summary.json      the numbers, each tagged with where it came from
├── decisions.jsonl   every admit/defer/reject, with the state it was decided on
└── outcomes.jsonl    per-request TTFT, inter-token gaps, deadline verdict
```

`bench report` turns a directory of those into a self-contained `report.html` —
verdict, caveats, figures, provenance — plus loose PNGs for a paper.

## A result

Qwen2.5-0.5B on an A10G, `max_num_seqs=4`, 80 requests at 15/s, two repeats:

| policy | admit % | TTFT p95 | goodput |
|---|---|---|---|
| `no_admission` | 100.0% | 2285ms ±725 | 0.319 |
| `queue_depth[max_waiting=8]` | 88.1% | 1668ms ±145 | 0.300 |
| `queue_depth[max_waiting=2]` | 63.1% | **951ms ±136** | 0.319 |

Shedding 37% of traffic cut tail latency **2.4× at identical goodput** — the
refused requests would have missed their deadline anyway. The spread says as
much as the median: unmanaged queueing is unpredictable, not merely slow.

**The signal mattered more than the threshold.** `kv_cache_usage_perc` never
exceeded 0.005 while the queue reached 24 deep — on a 0.5B model a KV-pressure
policy reads a flat line and silently becomes admit-everything. Which signal
carries the pressure depends on the regime, which is why policies declare what
they need. Full numbers: [`docs/results.md`](docs/results.md).

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

`requires` is how a policy declares the signals it needs. If the engine cannot
report one, the run stops at startup rather than reading the missing value as
zero and quietly behaving as an admit-everything baseline.

Policies in **your own** pip package are discovered automatically — no edit to
this repo. `admitperf policies` lists everything resolvable.

```toml
[project.entry-points."admitperf.policies"]
my_policy = "my_pkg.policies:MyPolicy"
```

## Installing

```bash
pip install -e '.[all]'          # everything, for working on the repo
uv sync --all-extras             # or reproduce exactly, from the lock
```

Or just the parts you need: `.` (the decision path — click, httpx, pyyaml,
rich), `[modal]`, `[dashboard]`, `[analysis]`, `[dev]`. The runtime dependency
list is deliberately short; putting admission control in front of a fleet
should not drag in a plotting stack.

Nothing else is required — the mock-engine path needs no configuration, and
Modal needs only your existing `modal setup` credentials. Gated weights need a
token, which lives in a Modal secret rather than a file:

```bash
modal secret create huggingface HF_TOKEN=hf_xxx
admitperf infra up --model meta-llama/Llama-3.1-8B --hf-secret huggingface
```

## Repo layout

```
src/admitperf/   core (the four objects, registry, runner) · engines · infra · bench
policies/        the policies under study, as a separate package
experiments/     example configs          dashboard/   the Streamlit app
docs/            motivation → design → metrics → results
reports/         written studies, each backed by a run bundle
scripts/         mock_vllm.py, a mock engine for testing without a GPU
```

`make test` · `make lint` · `make dashboard` · `make mock-engine` · `make diagrams`. Architecture in
[`docs/architecture/`](docs/architecture/), following the
[C4 model](https://c4model.com); the `.mmd` files are the source of truth.

## Documentation

| Read this to... | Go here |
|---|---|
| Understand why AdmitPerf exists | [`docs/motivation.md`](docs/motivation.md) |
| See what exists and what does not | [`docs/status.md`](docs/status.md) |
| See the first real-hardware results | [`docs/results.md`](docs/results.md) |
| Read the data-flow + reproducibility contract | [`docs/design.md`](docs/design.md) |
| Read the metric definitions, and what is not measurable | [`docs/metrics.md`](docs/metrics.md) |
| See which policies fit behind this API, and the candidate list | [`docs/scope.md`](docs/scope.md) · [`docs/policies.md`](docs/policies.md) |
| See what each policy does, without reading code | [`src/admitperf/policies/README.md`](src/admitperf/policies/README.md) |
| Explore results interactively | [`dashboard/README.md`](dashboard/README.md) |
| Read the written studies, and the plan | [`reports/`](reports/) · [`docs/PROPOSAL.md`](docs/PROPOSAL.md) |
| Read the adversarial review of the framing | [`docs/prior-art/adversarial_review.md`](docs/prior-art/adversarial_review.md) |
| Understand where the design came from | [`docs/lineage.md`](docs/lineage.md) |

## Contributing

New policies, workloads, engine adapters and metrics are welcome.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the adapter contract, the fidelity rule
for reference-policy ports (never claim `"CONCUR"` when you mean
`"CONCUR-inspired"`), conventions and PR flow.

## Authors

**Harshul Jain** · **Dr. Tanmay Sah** · **Tanya Sah** · **Parv Khatri**
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

MIT for code, CC-BY 4.0 for docs and results bundles. Companion survey of the
admission-control literature: preprint pending.
