# Spec: AdmitPerf as the survey's reference implementation

Status: Draft
Last updated: 2026-09-17

## Summary

AdmitPerf is one of three things the survey contributes. The taxonomy describes
policies, the contract says what to report about them, and this harness makes
the contract executable and produced the evidence that the contract is
necessary. Every feature below traces to one of those two jobs. Anything that
traces to neither is out of scope, however interesting.

## Design Decisions

### D1: Contract items map one-to-one onto executable checks

The paper's answer to "why this minimum" is "because we watched each one break".
The repo's answer to "how do I comply" must be "run this". Each item therefore
has exactly one mechanism, and no item is left as advice.

| Contract item | Mechanism | State |
|---|---|---|
| Load as a multiple of measured capacity | `bench capacity` + `capacity_ref` in the manifest | built |
| Deadlines as a multiple of unloaded latency | `infra calibrate` + `slo_mode: relative` | built, never run |
| Parameter provenance: fitted vs inherited | `parameter_provenance` in the manifest | **to build** |
| Signal liveness | `signal_range` + `policy_requires` | built |
| Three or more repeats, spread reported | `bench compare`, `bench decide` | built |
| Context budget vs trace token sizes | `check_context_budget`, refuses to start | built |
| Metric definition | offered attainment, rejections counted as misses | built |
| Per-tenant fairness | `bench fairness` | **to build** |

Two gaps. Both are small, and both are items the paper will claim the reference
implementation enforces, so neither is optional.

### D2: The second port is FluidWait-inspired, not Scorpio-inspired

**Chosen**: FluidWait first. Scorpio noted as the stronger follow-up.

**Why**: G3 needs a second published policy whose signal *moves on our
hardware*. `docs/policies.md` says the concurrency cap binds in our regime and
`waiting_requests` is what moves; FluidWait reads queue depth, so it will act.
Scorpio is the better scientific target, being peer-reviewed, deadline-aware and
dual-gate, but its admission test depends on a fine-tuned length predictor
(OPT-125M, 100 bins). That is a model to train and host before a single decision
can be made, which is a project rather than a task.

**Revisit if**: the FluidWait port produces no sensitivity. Then the objection
"you picked a fragile specimen" stands and Scorpio becomes worth its cost.

### D3: Ports state their deviations, and the deviations are the survey's data

Every port ships a list of the places the source paper did not say enough to
implement without a choice. Chronos's list already contains the cost constants
that swing its admit rate from 1.8% to 100%.

These lists are evidence, not apology. They go in the paper's appendix as the
concrete form of "papers underspecify". A port with no deviation list is either
bit-exact with its source or has not been read carefully.

### D4: Scorpio's metric is adopted as corroboration, not as novelty

Scorpio computes goodput with all arriving requests in the denominator, so
rejected requests count as SLO misses. That is what AdmitPerf calls offered
attainment, arrived at independently.

The paper should cite this rather than present the metric as new. It is the
strongest available argument that the metric item in the contract is right: two
independent efforts reached the same discipline because the alternative flatters
shedding.

### D5: Broken runs are kept, labelled, and cited

A run whose configuration was wrong is evidence about configuration. It is kept,
its defect is named in the caption, and it is never presented as a performance
result. The overwrite guard exists so that such a run cannot be destroyed by the
next one, which has already happened once.

### D6: Reproduction is a command, not an instruction

The appendix lists one command per table or figure. Each runs from a clean
checkout at a named commit. Where a command needs a GPU, that is stated with its
approximate cost.

## Architecture

```
  paper claim                     this repo
  ------------------------------  --------------------------------------
  contract item 1..6         ->   an executable check per item (D1)
  sensitivity rows 1..6      ->   experiments/*/results/, bundles + commit
  metric definitions         ->   bench compare, bench decide, bench fairness
  "papers underspecify"      ->   port deviation lists
  reproduction appendix      ->   one command per table
```

## Data Model

Manifest fields the contract depends on:

| Field | Purpose | State |
|---|---|---|
| `environment.local.code.commit` | which code produced this | built |
| `signal_range`, `policy_requires` | signal liveness | built |
| `capacity_ref` | load as a multiple of capacity | built |
| `situation`, `offered_rate`, `workload` | the load axis | built |
| `parameter_provenance` | fitted vs inherited, per parameter | **to build** |

## Edge Cases

- A policy that reads no signal: `signal_range` absent, not zero.
- Single-tenant workload: fairness undefined, not 1.0.
- Capacity probe never fails: report "at or above the top rung", invent nothing.
- A run with mostly failed scrapes: excluded from sensitivity rows, since it
  measures the harness.

## Not in v1

Agent-session workloads, QLM and CONCUR ports, multi-hardware sweeps, the
head-to-head study, and any leaderboard.

## Open Questions (resolved)

- **Q1** -> FluidWait, per D2.
- **Q2** -> Fairness ships with a definition, an implementation and a worked
  example, but no headline measurement: the workload assigns tenants without
  differentiating their SLOs. Stated as a limitation.
- **Q3** -> One commit for the whole paper, named in the appendix. Per-table
  commits imply the tables were produced at different times, which invites a
  question we do not want to answer.
