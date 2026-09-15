# experiments/

Experiment configs. Each one is self-contained: what to run on, what traffic to
send, which policies to compare, how many repeats.

| Config | What it is for |
|---|---|
| `demo.yaml` | Queue pressure on one small GPU. The shape that produced the first real results. |
| `chronos.yaml` | Deadline-aware admission against an uncontrolled baseline. See `reports/chronos-reproduction.md`. |
| `sweep.yaml` | The same comparison across several deployments, via `bench sweep`. |
| `multi-gpu.yaml` | Tensor-parallel across four GPUs. |

```bash
admitperf infra up   -c experiments/demo.yaml
admitperf bench run  -c experiments/demo.yaml
admitperf bench compare results/
admitperf infra down
```

A config with a `matrix:` section is run with `bench sweep` instead, which
provisions each entry in turn and tears it down afterwards.

Results land in `results/` (gitignored) — one directory per run, each recording
the resolved config that produced it.
