# experiments

An experiment says **what load to send**. It does not describe infrastructure — the
cluster config it points at does that, including which admission policy is active.

```
experiments/signal-liveness/
  experiment.yaml      cluster: infra/config/single.yaml
                       load: {kind, n, rate, seed, prompt_tokens, output_tokens}
                       baseline: no_admission
                       repeats: 3
                       expect: {reaches_threshold: true}
  results/             one directory per arm per repeat, written by `admitperf run`
```

```bash
admitperf run experiments/signal-liveness --mock              # fake engine, no GPU
admitperf run experiments/signal-liveness --mock --capacity 400   # never saturates
admitperf run experiments/signal-liveness --engine-url http://127.0.0.1:8080
```

Each arm writes `decisions.jsonl` and `report.json`. The report is the artifact;
every renderer and the dashboard read it and compute nothing.

## Writing one

Four things, and the third is the one people get wrong.

**Point at a cluster config.** To test a different policy, point at a cluster
config that declares it. To test a different topology, point at `pair.yaml` or
`disagg.yaml`. The experiment file does not change.

**Fix the seed.** Every arm then faces the same trace. Without it, a difference
between policies may be a difference between traces.

**Make requests long enough to overlap.** Concurrency is `rate x duration`, so
short requests cannot fill a cache at any arrival rate. This is the most common
reason a run comes back INERT, and the fix is the load rather than the policy.

**Declare what you expect, before running.** `expect:` is recorded in the report so
the prediction cannot be edited to match the result.

## There are no past results here

Earlier experiments were deleted rather than archived. They ran on a provider that
has been removed, through a harness that was never validated, with a policy
implementation that has been deleted. A number is evidence only if the thing that
produced it can be run again, and none of those could be.

See [`../src/admitperf/docs/status.md`](../src/admitperf/docs/status.md).
