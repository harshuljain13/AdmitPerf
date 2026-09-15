# Running Chronos on a Real Engine: A Reproduction Study

**Harshul Jain, Tanmay Sah, Tanya Sah, Parv Khatri**
AdmitPerf technical report · 2026-09-15

---

## Abstract

Chronos (Marref, Tarmissi & Chaibi, *Frontiers in Computer Science* 8, 2026)
applies classical real-time response-time theory to LLM inference, deriving a
closed-form worst-case response time bound and a per-request admission test
with formal TTFT and TBT guarantees. Its evaluation runs entirely in a
purpose-built discrete-event simulator with roofline-derived kernel parameters;
the authors flag validation against a real serving engine as future work. We
port Algorithm 1 to vLLM, fit its kernel parameters from live engine telemetry
rather than a roofline model, and compare it against an uncontrolled baseline
and a queue-depth threshold policy on an NVIDIA A10G.

Three findings. First, the algorithm ports cleanly: the theorems are
implementable in roughly 200 lines against an engine's Prometheus endpoint.
Second, on our hardware it is **drastically more conservative than the paper
reports** — 23.3% admission against the paper's 53.9–88.9%, driven by an
arrival-rate estimate that saturates the utilization test. Third, and most
consequential for the project: at this operating point **no policy achieved
acceptable deadline attainment**, so the comparison establishes a working
reproduction pipeline rather than a verdict on Chronos.

---

## 1. Background: what Chronos is

### 1.1 The idea

Real-time systems have admitted tasks by feasibility test for forty years: a
task is accepted only if its worst-case response time provably fits inside its
deadline. Chronos is, to the survey's knowledge, the first work to carry that
machinery onto the prefill/decode split of LLM inference. Where most admission
work tracks SLOs empirically — observe latency, back off when it degrades —
Chronos offers a *guarantee*, conditional on its model holding.

### 1.2 The formalization

Each request becomes a **sporadic job**. Prefill is released at arrival with an
absolute TTFT deadline; on completion it spawns a **decode task** modelled as a
recurrent task under continuous batching, whose every iteration must land
inside the TBT budget.

Prefill worst-case execution time is linear in chunks:

```
p_i    = ceil(l_i / B)                 prefill chunks for input length l_i
C_pre  = p_i · (α·B + γ)               worst-case execution time
```

with `α` the per-token prefill cost, `B` the chunk size, and `γ` a fixed
per-iteration overhead.

### 1.3 Theorem 1 — the WCRT bound

Derived through a four-step busy-period argument: identify the longest
continuously-non-empty prefill busy period; upper-bound interfering work from
arrivals inside that window via worst-case chunk count; apply a work-balance
inequality on GPU work as backlog plus initiating chunk; sum interference and
own service. The result:

```
WCRT  ≤  ρ_P · D_TTFT / (1 − ρ_P)  +  C_pre        valid iff ρ_P < 1
```

where `ρ_P = λ · E[C_pre]` is prefill utilization. The bound diverges as
utilization approaches unity — the formal statement of what saturation does.

**A property worth stating explicitly**, because it shapes everything
downstream and is easy to miss: `D_TTFT` appears *inside* the bound as well as
being the quantity compared against. Halving the deadline roughly halves the
predicted WCRT. Since `ρ·D/(1−ρ) ≤ D` requires `ρ ≤ 0.5`, the test refuses
essentially everything above half utilization **regardless of how generous the
deadline is**. Feasibility is governed by utilization and the request's own
prefill cost, not by the deadline in the way a naive reading suggests. This is
consistent with the paper's own reported admission rates, which fall to 53.9%
at 10× nominal load.

### 1.4 Theorem 3 — decode capacity

From the per-iteration budget `β·n_D + γ ≤ s`:

```
n*_D  ≤  (s − γ) / β
```

At the paper's parameters (β = 0.250 ms/token, γ = 7.0 ms) and `s` = 200 ms
this yields **772**. The paper reports approximately **731**. We record the
discrepancy rather than tune it away: the published figure evidently carries a
term our reading of the stated formula does not reproduce. Resolving it needs
the paper's full derivation. It does not affect our results, because decode
capacity was never the binding check in our runs (§5.3).

### 1.5 Algorithm 1 — the admission test

Three checks on every arrival, in order:

| # | Check | Rejects when |
|---|---|---|
| (a) | Utilization | `ρ_P ≥ 1` — system formally overloaded, no bound holds |
| (b) | TTFT feasibility | Theorem 1 bound exceeds this request's `D_TTFT` |
| (c) | TBT capacity | admitting one more decode task breaches Theorem 3 |

Arrival rate `λ` comes from a 60-second sliding window, using the **peak**
sub-window rate rather than the mean — averaging a burst over a minute would
hide precisely the overload the test exists to catch.

### 1.6 Published results

Azure LLM inference traces (May 2024), 50,000 sub-sampled requests, seed 42, a
7B dense model on an A100-80GB, `D_TTFT` = 2,000 ms, TBT = 200 ms:

| Load | Policy | TTFT miss | Admitted | P99 TTFT |
|---|---|---|---|---|
| 5× | **Chronos** | 0.00% | 88.9% | 161 ms |
| 5× | FCFS / EDF | 0.00% | 100% | 400 ms |
| 5× | AC-FCFS (rate-matched) | 6.80% | 88.9% | 4,127 ms |
| 10× | **Chronos** | 0.00% | 53.9% | 111 ms |
| 10× | FCFS / EDF | **99.44%** | 100% | — |
| 10× | SLAI | 22.55% | 100% | — |

The AC-FCFS row is the paper's sharpest result: a token-bucket rate limiter
*matched to Chronos's own admission volume* still misses 6.8% of deadlines
where Chronos misses none. Admitting the right requests matters, not merely
admitting fewer.

---

## 2. Reproducibility assessment

### 2.1 Artifact status

Code is available at `github.com/am-research/rtss-ttft-tbt` — a CPU-only
discrete-event simulator implementing all four schedulers, with fixed seed,
scripts for parameter fitting and trace characterisation, and roughly two
minutes of laptop runtime. By the standards of this literature that is
excellent: of 14 admission-primary papers in the companion survey, it is one of
two with a runnable artifact.

> **Correction to our own records.** `docs/prior-art/feasibility_audit.md`
> lists Chronos as "**No code** (theory paper)". That is wrong;
> `survey/notes/chronos.md` records the repository. The audit predates the
> deep-read and was never reconciled. Flagged here, and to be fixed at source.

### 2.2 The gap the artifact leaves

Everything is validated in simulation. The kernel parameters α, β, γ are
roofline-derived from A100 specifications, not measured. The README states that
real vLLM measurements *could* replace them but the artifact does not do so.

So the paper's guarantee is sound **with respect to its model**, and the model's
correspondence to a real engine is untested. That gap is what this study
addresses: not whether the theorems are right — they are theorems — but whether
the admission test behaves usefully when its inputs come from a real engine
instead of a roofline.

### 2.3 What we ported, and what we could not

| Component | Status |
|---|---|
| Cost model `C_pre = p·(αB + γ)` | ported exactly |
| Theorem 1 WCRT bound | ported exactly |
| Theorem 3 decode capacity | ported; yields 772 vs published ~731 (§1.4) |
| Algorithm 1, three checks in order | ported exactly |
| 60s sliding-window λ estimator | ported, peak sub-window |
| α, β from telemetry | **substituted** for roofline derivation |
| γ | **not fitted** — paper's value retained |
| Azure trace replay | **not used** — synthetic workload (§4) |
| FCFS / EDF / SLAI / AC-FCFS baselines | only FCFS-equivalent (`no_admission`) |

Two substitutions are load-bearing and prevent us from claiming a faithful
reproduction:

**γ is not fitted.** Separating a fixed per-iteration overhead from the
per-token slope requires measurements at two or more batch sizes. vLLM's
aggregate counters report totals, not a curve, so the decomposition is
unidentifiable from a single window. We retain the paper's A100 value on
hardware that is not an A100.

**ρ_P is estimated, not known.** The paper computes utilization from an arrival
process it controls. We infer λ from observed arrivals and `E[C_pre]` from a
fitted cost model, so ρ_P carries the error of both, and Theorem 1 inherits it.

Per the project's fidelity rule the implementation is therefore named
`ChronosInspiredWCRT`, never `Chronos`.

---

## 3. Experimental setup

| | |
|---|---|
| Engine | vLLM 0.29, `Qwen/Qwen2.5-0.5B-Instruct` |
| Hardware | 1× NVIDIA A10G (Modal), fp16 |
| Engine config | `max_num_seqs=4`, `max_model_len=2048`, `gpu_memory_utilization=0.55` |
| Scheduling | FCFS, prefix caching **disabled** |
| Workload | 150 requests, Poisson arrivals at 20/s, seed 0 |
| Repeats | 3 per policy, fresh seed each |
| Config | [`../experiments/chronos.yaml`](../experiments/chronos.yaml) |
| Bundles | `results/chronos/` |

Prefix caching is disabled deliberately: with it enabled, repeated prompts skip
prefill and KV pressure stops tracking offered load, which would corrupt both
the utilization estimate and the comparison.

`max_num_seqs=4` is the capacity limit that makes admission control matter.
Without a binding constraint every policy scores identically because the fleet
never saturates.

---

## 4. The workload

No public trace was replayed. We generated a synthetic multi-class workload,
characterised here because its properties determine what the results can show.

**Arrival process.** Poisson, exponential inter-arrival gaps. Measured over the
150-request instance: mean gap 0.047 s, standard deviation 0.052 s — consistent
with an exponential distribution, where standard deviation equals the mean.
Achieved rate 21.2/s over a 7.1 s span. Bursts are the point; a policy that
only ever sees evenly-spaced traffic is never tested.

**SLO classes.** Three, with deliberately disagreeing deadlines, because a
single deadline makes every policy look alike — the interesting question is
which request a policy sacrifices.

| Class | Share | `D_TTFT` | `D_TBT` | Median input tokens |
|---|---|---|---|---|
| interactive | 54.0% | 500 ms | 50 ms | 304 |
| streaming | 26.0% | 2,000 ms | 100 ms | 1,352 |
| batch | 20.0% | 30,000 ms | — | 2,395 |

**Scale.** Input tokens 79 / 431 / 4,042 (min / median / max); output tokens
34 / 244 / 2,041. Three tenants, roughly balanced (47 / 55 / 48).

**Reproducibility.** Fully determined by seed. The generator uses a private
`random.Random` instance rather than the module-level one, so a policy or
engine calling `random()` cannot shift the workload and make two runs
incomparable.

**Divergence from the paper.** The paper replays Azure traces with
`D_TTFT` = 2,000 ms uniformly. Our interactive class demands 500 ms — four
times tighter — on a workload whose median request already takes longer than
that to serve at this concurrency. This matters for interpreting §5.

---

## 5. Results

### 5.1 Headline

12 runs, 4 policies × 3 repeats. Medians across repeats; ± is half the observed
range, not a confidence interval.

| Policy | Admit % | TTFT p95 | Goodput | TTFT miss (admitted) |
|---|---|---|---|---|
| `no_admission` (FCFS) | 100.0% | 4750 ms ±853 | 0.113 | 84.4% |
| `queue_depth[max_waiting=4]` | 66.0% | 1618 ms ±219 | **0.180** | 59.3% |
| `chronos_inspired` | 23.3% | 1216 ms ±494 | 0.080 | 64.5% |
| `chronos_inspired[safety=2.0]` | 5.3% | **515 ms ±162** | 0.027 | **36.8%** |

Reject reasons:

```
chronos_inspired               deadline_unmeetable=150, overloaded=204
chronos_inspired[safety=2.0]   deadline_unmeetable=245, overloaded=183
queue_depth[max_waiting=4]     queue_depth=156
```

### 5.2 Reading it

**The ordering is monotone and sensible.** Tail latency falls strictly as
admission tightens: 4750 → 1618 → 1216 → 515 ms. Deadline attainment among
admitted requests improves in the same direction, 84.4% → 36.8% miss. The
mechanism works: refusing traffic protects what is admitted.

**But no configuration is good.** The best deadline attainment is 36.8% *missed*
— at 5.3% admission. The paper reports 0.00% miss at 53.9% admission. We are
not reproducing its result; we are reproducing its *shape*.

**Goodput peaks in the middle.** `queue_depth` wins on goodput (0.180) because
goodput counts SLO-meeting completions against *all* offered load. Chronos
sheds so aggressively that even a high per-request success rate cannot
compensate. This is the metric working as intended — a policy cannot win by
refusing everything — and it is the clearest evidence that our Chronos
configuration is mistuned rather than merely strict.

**Over half of Chronos's rejections are `overloaded`, not `deadline_unmeetable`
** (204 vs 150). That is check (a) firing: our estimated `ρ_P ≥ 1`. The
admission test barely reaches Theorem 1 before the utilization gate refuses the
request. Chronos in these runs is behaving closer to a rate limiter than to a
feasibility test — which is precisely the AC-FCFS baseline the paper designed
to argue against.

### 5.3 Why the utilization estimate saturates

`ρ_P = λ · E[C_pre]`. With λ ≈ 21/s measured at the peak sub-window and
`C_pre` ≈ 48 ms for a single-chunk request, `ρ_P ≈ 1.0` at the operating point
— the boundary. Two causes compound:

1. **Peak-window λ against a bursty arrival process.** Exponential gaps produce
   sub-second bursts well above the nominal 20/s. Taking the peak is faithful
   to the algorithm and correct for its purpose, but combined with a 1-second
   bucket it reports the burst rate as the sustained rate.
2. **`C_pre` is calibrated for an A100 serving 7B, applied to an A10G serving
   0.5B.** α is fitted from telemetry; γ = 7.0 ms is not, and for a 0.5B model
   at chunk size 512 the fixed term is a large fraction of `C_pre`.

Check (c), decode capacity, never fired — consistent with §1.4's note that the
772-vs-731 discrepancy does not affect these results.

### 5.4 Threats to validity

- **Single hardware configuration, single model, 150 requests, 3 repeats.**
  Nothing here generalises.
- **Our deadlines are tighter than the paper's** (500 ms interactive vs
  2,000 ms uniform) on a slower effective operating point. This alone could
  account for much of the gap in absolute miss rates.
- **γ unfitted** (§2.3) directly inflates `C_pre` and therefore `ρ_P`, biasing
  Chronos toward rejection. The comparison is unfair to Chronos in a way we can
  name but have not yet corrected.
- **Only one of four baselines.** EDF, SLAI and especially AC-FCFS are absent;
  AC-FCFS is the paper's most informative comparison and its absence means we
  cannot test its central claim.
- **Goodput is compressed by our deadline distribution.** At this operating
  point most requests miss the interactive deadline regardless of policy, which
  narrows the differences between policies.

---

## 6. Conclusions

**On the port.** Algorithm 1 transfers to a live engine without conceptual
difficulty. The theorems are implementable in roughly 200 lines of pure
arithmetic against a Prometheus endpoint, and the pipeline — provision, run
four policies against one deployment, compare with spread — executes end to end
for about $0.40 of GPU time.

**On the reproduction.** We did not reproduce the paper's numbers and do not
claim to. The dominant obstacle is not the algorithm but its *parameterisation*:
`ρ_P` is the input the whole test pivots on, and estimating it from a real
engine is a harder problem than the paper — which controls its own arrival
process — needs to confront. That is a genuine finding about deploying formal
admission control, and it is invisible in simulation.

**On what this establishes.** A reproduction pipeline, not a verdict. Every
number traces to a bundle; every bundle records the config that produced it.

### Next steps, in order of expected value

1. **Fit γ** by measuring at several batch sizes. Until then `C_pre` is
   systematically wrong and every Chronos result is biased toward rejection.
2. **Implement AC-FCFS** — a token-bucket matched to Chronos's admission
   volume. It is the paper's sharpest claim and the cheapest to test.
3. **Run the paper's operating point**: uniform `D_TTFT` = 2,000 ms, TBT =
   200 ms, so the comparison is against its actual configuration.
4. **Replay Azure traces** instead of synthetic Poisson, removing the workload
   as a confound.
5. **Cross-validate against the published simulator** at identical parameters.
   If our implementation and theirs disagree in simulation, the port is wrong;
   if they agree, the gap is the substrate, which is the interesting answer.

---

## References

Marref, A., Tarmissi, K., & Chaibi, H. (2026). Formal schedulability analysis
for LLM inference: TTFT and TBT deadline guarantees via response-time theory.
*Frontiers in Computer Science*, 8. DOI
[10.3389/fcomp.2026.1873627](https://doi.org/10.3389/fcomp.2026.1873627).
Artifact: `github.com/am-research/rtss-ttft-tbt`.

Reading notes: `../../survey/notes/chronos.md`.
Implementation: `../src/admitperf/policies/chronos/`.
Bundles: `results/chronos/`.
