# Dashboard

```bash
pip install -e '.[dashboard]'
streamlit run dashboard/app.py -- --results results/
```

Reads the bundles `bench run` writes. Needs no GPU and no running harness, so
results can be copied off a machine that has since been torn down.

## What each tab is for

| Tab | Question it answers |
|---|---|
| **Comparison** | Which policy won, and how much of that was just refusing traffic |
| **The trade-off** | What refusing bought in latency, and what it cost in attainment |
| **Over time** | What the policy could see, and what it did about it |
| **Why refused** | Which check refused each request, and which promise was missed |
| **Data** | Every run, unaggregated, downloadable as CSV |

## Two rules it enforces

Both carried over from the CLI, because they are what keep a comparison honest.

**A deployment is the unit of comparison.** Policies are only comparable when
they faced the same engine on the same hardware. The sidebar defaults to one
deployment; selecting more is allowed but the page says plainly that
differences between them may be the machine rather than the policy.

**Offered attainment is the headline, served is shown beside it.** Served alone
flatters shedding — refuse 95% of traffic and serve the rest perfectly and it
reads 1.00. The comparison chart puts both bars side by side for exactly this
reason: a large gap means heavy refusal, and whether that was worthwhile is the
offered bar.

## Things it will tell you that a table would not

- **Degraded runs are hidden by default.** If most engine scrapes failed, the
  policy was deciding on stale state and the numbers describe the workload, not
  the policy. Unhiding them prints a warning.
- **A flat signal is called out.** On the timeline, if the chosen signal never
  changed during a run, the app says so — a policy reading a flat line looks
  identical to one that decided everything was fine.
- **Single-run policies are flagged.** No spread means no basis for trusting a
  difference.
- **Unmeasurable metrics are named with the reason**, so a reader can tell a
  metric that is zero from one that was never obtainable.

## Theme

`theme.py` uses the same palette as the C4 diagrams in `docs/architecture/`, so
a screenshot here sits beside a figure from the docs without looking borrowed.
Streamlit's own colours are set in `.streamlit/config.toml`.
