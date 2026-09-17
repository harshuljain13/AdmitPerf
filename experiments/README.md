# experiments/

**One folder per experiment, holding everything about it.**

```
experiments/which-policy-when/
├── experiment.yaml     what to run on, what traffic, which policies, how many repeats
├── results/            every run this experiment has produced
│   ├── <policy>-r<n>/    manifest.json + summary.json  (committed)
│   │                     decisions.jsonl + outcomes.jsonl  (local only, large)
│   ├── compare.txt       the table
│   ├── report.html       the readable artifact
│   └── figures/          PNGs for a paper
└── README.md           (optional) what this experiment found
```

Pass the **folder**, not the file — results then land inside it by default, and
an experiment stops being scattered across two trees:

```bash
admitperf bench run -c experiments/which-policy-when
```

A run refuses to write into a results directory that already holds runs. We
lost a result to a silent overwrite once; a bundle is the only record of what a
number meant. Move the old results aside, or pass `--force` if you truly want
them replaced.

What is committed: the manifest, the summary, the comparison table, the report
and the figures — everything a paper cites. What is not: the per-request logs,
which are large and regenerable.

**Named for what they produce.** A file called `Experiment1` tells you nothing
six weeks later, and neither does the results directory it writes — which takes
its name from the config. The name should be the answer you are going to quote.

| Config | What it produces |
|---|---|
| `which-policy-when.yaml` | **The decision table**: every policy under four loads, read with `bench decide` as situation -> policy. Start here if the question is "which algorithm". |
| `half-capacity-headroom.yaml` | Evidence for what happens *below* capacity — where no policy beats admitting everything, and refusing is pure loss. |
| `shedding-vs-tail-latency.yaml` | Does refusing traffic cut the tail, and what does it cost? The shape that produced the first real-hardware results. |
| `chronos-vs-no-admission.yaml` | The Chronos reproduction table. See `reports/chronos-reproduction.md`, including the addendum on cost-model sensitivity. |
| `same-policies-across-gpus.yaml` | One comparison table per deployment, never pooled. Run with `bench sweep`. |
| `tensor-parallel-4x.yaml` | The same questions on a four-GPU tensor-parallel deployment. |

```bash
admitperf infra up   -c experiments/shedding-vs-tail-latency
admitperf bench run  -c experiments/shedding-vs-tail-latency
admitperf bench compare results/
admitperf infra down
```

A config with a `matrix:` section is run with `bench sweep` instead, which
provisions each entry in turn and tears it down afterwards.

Results land in `results/` (gitignored) — one directory per run, each recording
the resolved config that produced it.
