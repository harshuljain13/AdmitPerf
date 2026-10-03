# Adversarial review — AdmitPerf against the survey

The survey defines the taxonomy and the reporting items. AdmitPerf is supposed to be
the instrument that fills in the one column the corpus leaves empty. This checks
whether it actually does, against `research/survey/docs/taxonomy.md`,
`docs/applicability.md` and `source_paper/body.tex`.

Reviewed as an adversary: where AdmitPerf's vocabulary was invented rather than
taken from the survey, it is called a defect, not a difference.

---

## What survives

**The seven reporting items are the survey's.** `report/items.py` implements
capacity-relative load, calibrated deadlines, signal liveness, repeats and spread,
parameter provenance, metric definition and configuration disclosure. Those are
`body.tex`'s enumerate block, in order. Not invented.

**Portability A/B is the survey's**, derived the same way: `ing` → A, decidable at
ingress from observable engine state; `qb`/`mf` → B, requires control over batch
formation. AdmitPerf's framing — a Class B policy cannot sit behind this interface —
is the operational consequence of that derivation.

**Liveness as the load-bearing item is the survey's central claim.** Of sixteen
admission-primary papers, six of seven columns are fully populated from the text and
the seventh is uniformly empty: *not one reports the observed range of the quantity
its policy reads.* AdmitPerf putting that verdict on line one is the right response.

---

## Defect 1 — the policy card is missing a column, and renames another

The survey's applicability table has **seven** columns. AdmitPerf's card has five.

| Survey column | AdmitPerf | Verdict |
| --- | --- | --- |
| Unit | `unit` | ok |
| Setting | `setting` | ok |
| SLO-awareness | `objective` | **renamed** |
| Signal quantity | `signal` | **wrong level, see Defect 2** |
| Metadata assumed | — | **MISSING** |
| Portability | `portability` | ok |
| Signal liveness | computed | ok |

**`objective` should be `slo_awareness`.** The survey's axis 3 is about what the
policy is trying to protect — deadline, throughput, fairness, cost — and it carries
two sub-branches AdmitPerf has no field for: *granularity* (job / request /
per-stage) and *fairness type* (task / tenant / priority-weighted). Renaming is
cosmetic; the missing sub-branches are not. Per-stage SLOs are flagged in the
taxonomy as a genuine gap with a single occupant, and a card that cannot express
them cannot describe that policy at all.

**`metadata_assumed` is absent entirely**, and it is the column that decides
whether a policy can run on a given workload. The survey's values: `none`, `SLO
class`, `deadlines`, `per-stage deadlines`, `session id`, `priority class`. Eight of
sixteen policies need nothing beyond the request; the rest need something the
workload must carry. AdmitPerf's `Request` dataclass happens to carry
`deadline_ttft_ms`, `slo_class`, `agent_id` and `priority`, so the information
exists — it is simply not declared or checked. A policy requiring deadlines against
a trace that has none will admit everything and look well-behaved.

---

## Defect 2 — `signal` conflates the taxonomy category with the field name

AdmitPerf's `signal = "kv_used_fraction"` is a field of `SystemState`. The survey's
signal quantity is `kv_pressure`, from a controlled vocabulary:

```
kv_pressure · queue_depth · deadline_slack · wait_estimate
predicted_length · batch_state · rate · analytic
```

These are different levels. The vocabulary is what makes two policies comparable
across papers; the field name is this implementation's plumbing. A card printing
`kv_used_fraction` cannot be placed in the survey's table without a human
translating it, which defeats the point of the card filling itself in.

Both are needed: the taxonomy term for the card, the field for reading the value.

**And AdmitPerf has no notion of signal *structure* at all.** The survey's axis 4
splits on it, and the split is the interesting part:

| Sub-branch | What it is | AdmitPerf |
| --- | --- | --- |
| 4a scalar heuristic | one scalar against a threshold | the only shape it supports |
| 4b dual-gate conjunction | two signals ANDed, to tell healthy high utilisation from congestion | **cannot express** |
| 4c LP-based composite | a linear program over many state variables | **cannot express** |
| 4d formal WCRT bound | closed-form bound plus admission test | **cannot express** |

`threshold: float` presumes 4a. CONCUR is the taxonomy's clean 4b entry — it ANDs
KV utilisation above `U_high` with prefix hit-rate below `H_thresh`, precisely
because high utilisation with a high hit rate is healthy reuse rather than
thrashing. AdmitPerf's single-scalar liveness check cannot describe that policy's
firing condition, let alone judge whether it was live.

This also means AdmitPerf's liveness verdict is **narrower than the survey's item**.
Item 3 asks for the observed range of the quantity the policy reads. For a dual-gate
policy that is two ranges and their conjunction; for an LP it is a feasibility
region. Reporting one scalar's range and calling it liveness is the right answer
only for 4a.

---

## Defect 3 — `goodput` is not computed, and the definition is specific

The survey defines it (Definition 2, Equation 1):

```
G = |{r in Admitted : SLO(r) = 1}| / |Offered|
```

The denominator is **offered**, not admitted. Rejected requests count as misses.
Reporting item 6 exists because both conventions circulate and give different
numbers for the same run, with the gap widening as the rejection rate rises.

AdmitPerf's report asserts `metric_denominator: "offered"` as a *string the caller
passes in*, and computes no goodput at all. The item is marked `ok` on the strength
of a claim rather than a calculation. That is the failure mode the survey documents,
reproduced inside the tool built to catch it.

---

## Defect 4 — the unit vocabulary is wrong

AdmitPerf: `request`, `batch`, `token`, `session`.
Survey axis 1: `request`, `agent-session`, `tenant`.

`batch` and `token` are not admission units in the taxonomy — batch formation is
what Class B policies control, not what they admit. And **`tenant` is missing**,
which is the whole of VTC, FairBatching and Equinox. `SystemState` already carries
`per_tenant_running` and `per_tenant_admitted_recent`, so the state exists for a
tenant-unit policy that the card cannot describe.

---

## Defect 5 — the cluster config is a seventh-column fact, and does not know it

`infra/config/base.yaml` holds the model, engine flags, topology and hardware. That
is reporting item 7, configuration disclosure: *model, engine version, hardware,
workload subset, offered load, SLO tuple, and the identity of the baseline actually
run.*

The report currently satisfies item 7 with `config_sha` — a filename. The config
carries everything the item asks for and the report quotes none of it. The item
should be computed from the config, not asserted with a hash.

---

## What this means for the claim AdmitPerf can make

The survey's Open Problem 7 is specific:

> Determining which of the seven are load-bearing, in the sense that omitting one
> allows a reported result to move without a reader detecting the change, is not
> answerable by reading. It requires running the same policy under controlled
> variation of each item in turn.

And on item 3 specifically:

> Establishing that requires running a policy whose signal is known to be inert
> alongside one whose signal is known to move, on the same deployment and against
> the same trace, and asking whether any reported number separates them.

**That experiment is `signal-liveness`, and the design is already right**: same
deployment, same seeded trace, one arm whose signal moves and one whose signal does
not. The gap is not the experiment. It is that the card describing it uses
vocabulary the survey does not, so the result cannot be placed in the table it is
meant to fill.

## In priority order

1. **Rename and complete the card** to the survey's seven columns, with
   `metadata_assumed` and `slo_awareness`, and the survey's controlled vocabularies.
2. **Separate signal quantity from signal field.** `signal_quantity: kv_pressure`
   for the card; `signal_field: kv_used_fraction` for reading the value.
3. **Compute goodput** to Definition 2, over offered. Stop asserting item 6.
4. **Fix the unit vocabulary**: `request`, `agent-session`, `tenant`.
5. **Compute item 7 from the cluster config** rather than quoting a filename.
6. **Declare signal structure** (4a–4d) and refuse to judge liveness for structures
   the single-scalar check cannot describe, rather than reporting a misleading
   verdict.

Items 1–4 are small. Item 6 is the honest one: AdmitPerf supports 4a, and should say
so rather than implying it can evaluate any policy in the corpus.


---

# Resolution

All six items executed 2026-10-03. What changed, and what is still honest about its
own limits.

| # | Defect | Status |
| --- | --- | --- |
| 1 | Card missing `metadata_assumed`, `objective` misnamed | **fixed** — seven columns, survey names |
| 2 | `signal` conflated quantity with field; no structure | **fixed** — `signal_quantity` + `signal_field` + `signal_structure` |
| 3 | Goodput asserted, not computed | **fixed** — Definition 2, denominator `offered` |
| 4 | Unit vocabulary wrong | **fixed** — `request`, `agent-session`, `tenant` |
| 5 | Item 7 satisfied with a filename | **fixed** — six fields, checked individually |
| 6 | Liveness judged for structures it cannot describe | **fixed** — verdict withheld |

**The schema went to 2.0.** The card's keys changed meaning, so a 1.0 report cannot
be rendered against them. Reports from before this change are refused by name rather
than part-read, and the dashboard says so.

Two consequences worth stating, because both make the report say LESS than it used
to:

**Reporting item 6 now comes back `n/a`.** The driver records no per-request latency,
so it cannot say which admitted requests met an SLO, so it cannot compute goodput.
It previously reported `ok` on the strength of a string the caller passed in. An
honest `n/a` is the improvement.

**`no_admission` reports UNKNOWN, not INERT.** The baseline declares no
`signal_quantity`, because it reads nothing. That is a different state from a signal
that existed and never moved, and conflating them would have made the baseline look
like a failed policy.

## Still not aligned

**Only scalar policies can be judged.** `JUDGEABLE_STRUCTURES` is `{"scalar"}`. A
dual-gate, LP or formal-bound policy gets its range reported and its verdict
withheld. That is honest rather than complete: the survey's axis 4 has four
sub-branches and AdmitPerf measures one. CONCUR, the taxonomy's clean dual-gate
entry, cannot be evaluated here — it ANDs KV utilisation above a bound with prefix
hit-rate below another, and the second signal is not in `SystemState` at all.

**Axis 3's sub-branches are declarable but unused.** `slo_granularity` and
`fairness_type` exist on the card; no shipped policy sets them, because none is
fairness-oriented or per-stage.

**The survey's own scope limit still applies.** Its central finding is bibliographic:
an absence in what was extracted from evaluation sections, not a measurement. OP7
asks which of the seven items are load-bearing, and answering it requires running
the same policy under controlled variation of each item in turn. `signal-liveness`
varies item 3 only.
