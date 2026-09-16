<p align="center">
  <img src="docs/assets/banner.svg" alt="AdmitPerf — benchmark-driven admission control layer for LLM inference" width="100%"/>
</p>

# AdmitPerf

*A benchmark-driven admission control layer for LLM inference.*

AdmitPerf is two things in one repository: an **admission control library** you put in front of vLLM / SGLang (it decides admit / defer / reject per request), and a **benchmark harness** that runs any policy against a real engine on real hardware and reports what it cost. Same code path in production and in the benchmark, so the numbers are trustworthy because you can also deploy them.

> **Why this exists** — Across 14 admission-primary papers surveyed, no two share a baseline, engine version, workload, or SLO definition. AdmitPerf makes head-to-head comparison possible. See [`docs/motivation.md`](docs/motivation.md).

**Status**: working MVP, verified on real hardware. First results in [`docs/results.md`](docs/results.md); honest gaps in [`docs/status.md`](docs/status.md).

---

## The loop

Two command groups, matching the two jobs: `infra` provisions, `bench` measures.

```bash
admitperf infra up   -c experiments/demo.yaml   # real vLLM on a real GPU
admitperf infra smoke                            # is it actually serving?
admitperf bench run  -c experiments/demo.yaml    # every policy, repeated
admitperf bench compare results/                 # who won, and by how much
admitperf infra down                             # stop paying for it
```

Every policy faces the **same deployment** — the server is started once and
nothing is re-provisioned between policies, so the accept/refuse decision is
the only thing that varies. That is what makes the comparison mean anything.

To vary the hardware or engine settings too, add a `matrix:` and use
`bench sweep`, which provisions each entry in turn and runs every policy
against it. Results stay grouped per deployment and are never pooled across
them — a table mixing an A10G row with an A100 row would be reporting the
machine rather than the policy.

```bash
admitperf bench sweep -c experiments/sweep.yaml   # provisions and tears down per entry
```

`infra up` writes the endpoint to `.admitperf/session.json`; `bench run` reads it. They are separate commands because loading a model takes minutes and every policy must face the *same* deployment — re-provisioning between policies would change the thing being controlled for. Already have an engine running? Skip provisioning with `--engine-url`.

Everything lives in one config file; flags override it for one-offs:

```yaml
name: queue-pressure
infra:
  gpu: A10G
  model: Qwen/Qwen2.5-0.5B-Instruct
  max_concurrent_inputs: 256
  engine:
    max_num_seqs: 4            # the cap that creates the queue
    tensor_parallel_size: 1
    enable_prefix_caching: false
    scheduling_policy: fcfs
workload:
  n: 80
  rate: 15
policies:
  - no_admission
  - {name: queue_depth, max_waiting: 2}
bench:
  repeats: 2
```

Each experiment writes a directory you can open:

```
results/<timestamp>-<experiment>/<policy>-r<n>/
├── manifest.json     what was run, against what, with which settings
├── summary.json      the numbers, each tagged with where it came from
├── decisions.jsonl   every admit/defer/reject, with the state it was decided on
└── outcomes.jsonl    per-request TTFT, inter-token gaps, deadline verdict
```

## Dashboard

```bash
pip install -e '.[dashboard]'
streamlit run dashboard/app.py -- --results results/
```

Interactive comparison, latency-versus-attainment trade-off, decision timelines,
and refusal breakdowns. It enforces the same two rules as the CLI: a deployment
is the unit of comparison, and offered attainment is the headline with served
shown beside it. Details in [`dashboard/README.md`](dashboard/README.md).

It also flags what a table would not — degraded runs are hidden by default, a
signal that never moved during a run is called out, and single-run policies are
marked as having no spread.

## Try it without a GPU

`scripts/fake_vllm.py` is a stdlib-only stand-in that speaks the three endpoints AdmitPerf touches. It gets busy under load — KV pressure rises with requests in flight and tokens slow down — so a policy has something real to react to.

```bash
python scripts/fake_vllm.py --port 8077                    # terminal 1

admitperf infra smoke --engine-url http://127.0.0.1:8077
admitperf bench run --engine-url http://127.0.0.1:8077 \
    --policy no_admission --policy queue_depth -n 60 --rate 25 --repeats 2
admitperf bench compare results/
```

None of the fake's timings mean anything about hardware — it exists to prove the wiring.

## Results from a real GPU

Qwen2.5-0.5B on an A10G, `max_num_seqs=4`, 80 requests at 15/s, two repeats:

| policy | admit % | TTFT p95 | goodput |
|---|---|---|---|
| `no_admission` | 100.0% | 2285ms ±725 | 0.319 |
| `queue_depth[max_waiting=8]` | 88.1% | 1668ms ±145 | 0.300 |
| `queue_depth[max_waiting=2]` | 63.1% | **951ms ±136** | 0.319 |

Shedding 37% of traffic cut tail latency **2.4× at identical goodput** — the refused requests would have missed their deadline anyway. The baseline's spread (±725 vs ±136) says as much as its median: unmanaged queueing is unpredictable, not merely slow.

**The signal mattered more than the threshold.** `kv_cache_usage_perc` never exceeded 0.005 while the queue reached 24 deep — on a 0.5B model the KV cache dwarfs what four short sequences can fill, so a KV-pressure policy reads a flat line and silently becomes admit-everything. Which signal carries the pressure depends on the regime, which is why policies declare what they need.

Full numbers and caveats: [`docs/results.md`](docs/results.md).

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

`requires` is how a policy says which signals it needs. If the engine cannot report one, the run stops at startup with a readable error instead of silently reading the missing value as zero and behaving as an admit-everything baseline. That is not hypothetical — it is precisely what a KV-threshold policy does on a model whose cache never fills.

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
│   └── baseline/      the null baseline only
├── policies/          the policies under study — separate package, see its README
├── experiments/       example configs (demo, chronos, sweep, multi-gpu)
├── reports/           written studies, each backed by a run bundle
├── docs/              motivation, scope, design, metrics, results, architecture
├── dashboard/         Streamlit app for exploring results
├── scripts/           fake_vllm.py — a pretend engine for testing without a GPU
├── tests/
├── Makefile           make test · make lint · make diagrams
└── CONTRIBUTING.md    how to add a policy, conventions, PR flow
```

Architecture in [`docs/architecture/`](docs/architecture/), following the [C4 model](https://c4model.com). The `.mmd` files are the source of truth; `make diagrams` re-renders the PNGs.

## Installing

```bash
pip install -e '.[all]'          # everything, for working on the repo
```

Or just the parts you need:

```bash
pip install -e .                 # the decision path: click, httpx, pyyaml, rich
pip install -e '.[modal]'        # + provisioning on Modal
pip install -e '.[dashboard]'    # + the Streamlit results app
pip install -e '.[dev]'          # + pytest, ruff, mypy
```

There is no `requirements.txt`. `pyproject.toml` declares what the project
needs, and `uv.lock` pins the exact versions that were resolved — a
hand-maintained third list would only drift from both. To reproduce an
environment exactly:

```bash
uv sync --all-extras            # installs from the lock, not the ranges
```

Every run also records the versions that produced it, in its `manifest.json`:
Python, platform, the client packages that did the timing, and the engine's own
reported version and resolved cache settings. A number that cannot say which
vLLM built it cannot be compared against a later one.

The runtime dependency list is deliberately short. Putting admission control in front of a fleet should not drag in a plotting stack.

### Environment

Nothing is required. The fake-engine path needs no configuration, and deploying an ungated
model to Modal needs only your existing `modal setup` credentials.

Gated weights (Llama, Gemma) need a token, which lives in a Modal secret rather than a
file — anything in the deploy environment is baked into the deployment:

```bash
modal secret create huggingface HF_TOKEN=hf_xxx
admitperf infra up --model meta-llama/Llama-3.1-8B --hf-secret huggingface
```

See `.env.example`.

---

## Documentation

| Read this to... | Go here |
|---|---|
| Understand why AdmitPerf exists | [`docs/motivation.md`](docs/motivation.md) |
| See which admission policies fit behind this API | [`docs/scope.md`](docs/scope.md) |
| See the C4 architecture | [`docs/architecture/`](docs/architecture/) |
| Read the data-flow + reproducibility contract | [`docs/design.md`](docs/design.md) |
| Read the metric definitions, and what is not measurable | [`docs/metrics.md`](docs/metrics.md) |
| Explore results interactively | [`dashboard/README.md`](dashboard/README.md) |
| See what each policy does, without reading code | [`src/admitperf/policies/README.md`](src/admitperf/policies/README.md) |
| Read the candidate policy list + fidelity rules | [`docs/policies.md`](docs/policies.md) |
| See the first real-hardware results | [`docs/results.md`](docs/results.md) |
| Read the written studies | [`reports/`](reports/) |
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

- Companion survey of admission-control literature — preprint pending
- The working vLLM gateway this design derives from, described in [`docs/lineage.md`](docs/lineage.md)

## License

MIT for code. CC-BY 4.0 for docs and results bundles.
