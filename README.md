# AdmitBench

An open benchmark for LLM admission-control policies.

AdmitBench replays real and synthetic LLM serving traces through pluggable admission-control policies, running against any OpenAI-compatible engine (vLLM, SGLang, TensorRT-LLM), and reports metrics that matter for both chat and agentic workloads — goodput, tail latency, agent-completion rate, preemption-loss ratio, and per-tenant fairness.

## Why

The admission-control literature has grown quickly — CONCUR, Fluid-WAIT, Aqua, FairBatching, Chronos, FastServe, ProServe, SOLA — but no two papers report numbers on the same workload, the same engine, or the same metric suite. Reviewers and practitioners cannot tell which policy wins where. AdmitBench fixes that by providing:

- A common `AdmissionPolicy` interface every published policy re-implements against.
- A trace corpus drawn from Azure LLM inference traces, LMSYS-Chat-1M, and public agent trajectories (SWE-bench, τ-bench, GAIA, ToolBench).
- A metric suite that measures agentic behavior — not just per-request TTFT.
- Reproducibility artifacts: seeds, configs, Modal recipes, and fidelity notes against each original paper.

## Status

Early scaffold. Not yet runnable. See `docs/design.md` for the target architecture.

## Install (planned)

```bash
uv pip install -e .
admitbench --help
```

## Layout

```
src/admitbench/
  policies/     # baseline re-implementations
  traces/       # trace loaders + synthetic generator
  metrics/      # measurement instrumentation
  harness/      # runner / orchestrator
experiments/    # experiment configs + notebooks
data/           # traces + results (gitignored)
docs/           # design, metrics, policies
tests/
scripts/
```

## Citation

Not yet published. Preprint pending.

## License

MIT. See `LICENSE`.
