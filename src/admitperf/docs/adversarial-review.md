# Adversarial review — AdmitPerf, 2026-10-03

Read as an adversary looking for reasons to reject a paper built on this tool. Every
finding below was reproduced, not inferred.

Companion to `survey-alignment.md`, which checked the vocabulary. This checks whether
the thing works.

---

## D1 — `bench run` runs a different experiment than the file describes

**Severity: critical. This invalidates any result produced through `bench run`.**

`ExperimentConfig.load` ignores keys it does not recognise and fills its own
defaults. Pointed at the current experiment schema it returns a config with no
relationship to the file:

| `experiments/signal-liveness/experiment.yaml` | what `bench run` would use |
| --- | --- |
| `cluster:` → `Qwen/Qwen2.5-7B-Instruct` | `model=Qwen/Qwen3-0.6B` (default) |
| `cluster:` → `admission.policy: kv_threshold` | `policies=[no_admission]` (default) |
| `load: {n: 90, rate: 12}` | `workload: n=200, rate=10` (default) |
| `repeats: 3` | `repeats=1` (default) |

Reproduce:

```python
ExperimentConfig.load("experiments/signal-liveness/experiment.yaml")
# -> model='Qwen/Qwen3-0.6B', gpu='A10G', policies=[no_admission], n=200, rate=10
```

Worse, it accepts anything:

```python
# a file containing only invented keys
ExperimentConfig.load(path)   # -> accepted, workload n = 200
```

A command that appears to run your experiment and runs something else is the worst
failure this codebase can have, and it is the failure the project exists to report
on. **The loader must refuse unknown keys**, and `bench run` must refuse a file in
the new schema rather than defaulting its way through it.

---

## D2 — two run paths coexist and disagree

`admitperf run` uses `bench/drive.py`. `admitperf bench run` uses `core/runner.py`.
They read different schemas, record different fields, and neither is marked as the
one to use. `core/runner.py` is the one explicitly distrusted — it is why
`drive.py` was written small enough to audit — and it is still shipped, still
reachable from the CLI, and still has four importers.

Pick one. Two instruments that disagree are worse than one that is wrong, because a
reader cannot tell which produced a number.

---

## D3 — `expect:` is recorded and never checked

The experiment declares a prediction:

```yaml
expect:
  signal: kv_used_fraction
  reaches_threshold: true
  limiter: kv
```

`drive.py` copies it into `report.json` and nothing compares it to what happened. The
point of writing a prediction down before the run is that it can be checked after,
and a prediction nobody checks is decoration. The report should state
`expected / observed / agreed?` per field.

---

## D4 — `require_saturation` is declared and ignored

`infra/config/base.yaml` carries:

```yaml
admission:
  require_saturation: true   # a run whose signal never moved is reported inert
```

Nothing reads it. Zero references in `src/` or `infra/`. The config promises a run
will fail when its signal never moved; no run fails. This is issue #12 described as
a setting rather than implemented.

---

## D5 — the offered rate is computed over the wrong interval

```python
offered_rps = n / wall        # drive.py:334
```

`wall` is measured from the first arrival to **after every worker thread has been
joined**, so it includes the drain time once arrivals have stopped. The run asked for
12/s and reports 11.19/s, a 7% understatement, while the arrival loop's worst lag was
only 5 ms — so the loop kept up and the rate is still wrong.

Reporting item 1 is capacity-relative load. It is built on this number.

The offered rate should be measured over the arrival window alone. Drain time belongs
in a separate field.

---

## D6 — no outcomes are recorded, so goodput can never be computed

`drive.py` fires each admitted request into a thread and discards the result. No
TTFT, no completion, no SLO attainment. Consequences:

- Reporting item 6 is **permanently** `n/a`, not pending.
- **Goodput-under-admission cannot be produced at all** — and that is the survey's
  headline metric (Definition 2), the thing an admission policy is supposed to
  improve.
- Reporting item 2, calibrated deadlines, is likewise unreachable: there is no
  latency to express relative to anything.

Three of the seven items are therefore unreachable by construction rather than by
circumstance, and the report presents them as merely unevidenced.

The tool can currently answer "did the signal move" and cannot answer "did the policy
help". Only the first is honest today.

---

## D7 — a hung request is silently abandoned

```python
for t in threads:
    t.join(timeout=120)
```

If a completion hangs, the join expires, the thread is left running, and nothing is
recorded. A run in which half the requests never returned reports the same decision
counts as one where all of them did. Count the timeouts and put them in the harness
block.

---

## D8 — eight tests assert on source text rather than behaviour

`tests/test_dashboard.py` contains eight `read_text()` assertions, including
`"file_uploader" not in source` and three checking exact English sentences. They pass
when the string is present and the behaviour is broken, and they fail on a rename
that changes nothing.

The verdict-meaning test is the worst case: it pins the wording of UI copy, so
editing a caption breaks the suite while the page still works. Test the rendered
output, or drop the test and keep the comment.

---

## What holds up

Said plainly, because an adversarial review that finds only faults is not a review.

**The renderer's refusals are real and well-tested.** 88 tests, most of them
negative: fp8 on sm80, weights leaving no KV, a topology holding under one sequence,
slicing, oversubscribed hosts, TP across hosts, 429 overflow, a missing private
address. Each names the offending number. This is the strongest part of the
repository.

**`report.json` as the artifact, with renderers as pure functions of it,** is the
right layering, and a test asserts all four formats agree on the verdict.

**The liveness distinction between INERT and UNKNOWN is correct** and it is subtle: a
signal that existed and never moved is a different finding from a policy that reads
nothing. The baseline lands in the second, as it should.

**Nothing claims to have been measured.** `docs/status.md` opens with "Measured
results: none" and lists what is unbuilt. For a repository this unfinished, that is
the most important page in it.

---

## In severity order

| | Defect | Effect |
| --- | --- | --- |
| D1 | `bench run` silently substitutes defaults | any result from it is invalid |
| D6 | no outcomes recorded | goodput and calibrated deadlines unreachable; 3 of 7 items dead |
| D2 | two disagreeing run paths | a reader cannot tell which produced a number |
| D5 | offered rate includes drain time | item 1 understates load by ~7% |
| D4 | `require_saturation` ignored | the config promises what nothing does |
| D3 | `expect:` unchecked | the prediction is decoration |
| D7 | hung requests uncounted | a broken run looks clean |
| D8 | source-text tests | pass while broken, fail on renames |

D1 and D6 are the two that would sink a paper. D1 because a reviewer who runs the
tool gets different numbers; D6 because the metric the field cares about cannot be
produced, and the tool does not say so plainly enough.
