# Dashboard

```bash
pip install -e '.[dashboard]'
streamlit run dashboard/app.py
```

Five pages, grouped by what you are doing.

| | Page | What it is for |
|---|---|---|
| **Set up** | Experiments | Build one — machine, traffic, policies, repeats — saved as YAML |
| | Algorithms | What each policy decides on, when it fits, and when it misleads |
| **Measure** | Run | Pick a config and an engine, watch it go |
| | Results | Read what happened |
| **Reference** | Terminology | Every term that appears in a chart or a rejection reason |

## Experiments

A form over the same four sections the config file has. It validates through
the *same* `ExperimentConfig` the CLI uses, so a setting that would fail at
deploy time fails here in milliseconds instead of ten minutes into
provisioning. Output is plain YAML you can commit, edit, or run from the CLI —
the form covers the common settings, not every one.

## Algorithms

Reads the live registry, so installing a policy makes it appear here without
anyone editing the page. Each entry pairs the mechanics with the part a
signature cannot tell you — **when it fits and when it misleads**. The
`kv_threshold` entry carries the warning we earned: on a small model the cache
never fills, so it reads a flat line near zero and silently becomes
accept-everything while appearing to work.

## Run

Shells out to `admitperf bench run` rather than importing the harness. The CLI
is the supported path, so this page exercises it rather than a parallel one,
and a long benchmark stays in a subprocess instead of blocking the app. Output
streams as it goes.

Three engine choices: the fake engine (free, no GPU, and the page tells you if
it is not running), a URL you paste, or a provisioned session.

## Results

Ordered by the questions a reader actually arrives with, not by what is easy to
plot:

1. **Which policy should I use, and by how much?** — stated in a sentence,
   before any chart
2. **Can I trust it?** — degraded runs, single-run policies and wide spread are
   flagged *above* the answer, not in a footnote
3. **What did refusing cost?** — latency against requests served, and wasted
   work
4. **Why did it refuse?** — reasons from the policy itself, and the signal it
   was reading
5. **Everything** — every run unaggregated, with CSV export

### Two rules it will not let you break

**A deployment is the unit of comparison.** Policies are comparable only when
they faced the same engine on the same hardware, so results are grouped by
deployment and never pooled across.

**"Served on time" is shown beside "of admitted, on time", never alone.** The
second flatters shedding — refuse 95% of traffic and serve the rest perfectly
and it reads 100%. The headline counts everything that *arrived*, so a policy
cannot improve it by refusing more, only by refusing better.

### Things it says that a table would not

- A signal that never moved during a run is called out — a policy reading a
  flat line looks identical to one that decided everything was fine.
- When nothing beats the baseline, it says so plainly and suggests raising the
  load rather than leaving you to infer it.
- Metrics that were never obtainable are named with the reason, so a zero is
  distinguishable from an absence.

## Terminology

A searchable glossary, grouped from concepts through to engine settings. It
exists because the pair that causes the most confusion —"on time" versus "of
admitted, on time" — looks interchangeable and is not. A test asserts that
every term used in the results pages is explained here, so a chart label
cannot appear without a definition behind it.

## Branding

`theme.py` takes every colour and the typeface from `docs/assets/banner.svg` —
near-black ground, admit-yellow, white, grey, Helvetica 900. The wordmark spells
*Admit* in yellow, which is why yellow also means admitted in every chart. A
test asserts the palette against the SVG, so editing the banner fails the suite
rather than letting the app drift.
