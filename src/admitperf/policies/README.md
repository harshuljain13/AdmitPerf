# AdmitPerf policies

What each policy decides on, what signal it needs, and where it misleads — so
you do not have to read the source to answer those questions.

```bash
admitperf policies        # the same list, from the CLI
```

To add one of your own **without touching this repo**, see [Adding your
own](#adding-your-own) at the bottom.

---

## What is here

### `no_admission`

Accepts everything. The baseline every other policy has to beat, and what vLLM
does out of the box.

---

### `queue_depth`

**Refuses when too many requests are already waiting.**

| | |
|---|---|
| Decides on | how many requests the engine has queued |
| Needs | `waiting_requests` |
| Parameters | `max_waiting` (default 8) |
| Refuses with | `queue_depth` |

Four lines of logic. It knows nothing about what any individual request asked
for, so it treats an interactive request and an overnight batch job the same.

**When it fits.** When the concurrency cap is what binds — a small model where
the engine's sequence limit is reached long before memory is.

**When it does not.** When requests have meaningfully different deadlines. It
cannot prefer the ones it could still serve on time.

---

### `queue_depth_defer`

Same rule, but **holds** the request and asks again shortly rather than
refusing outright. Converts a refusal into latency. Whether that is an
improvement depends entirely on whether your callers would rather wait than be
told no.

Parameters: `max_waiting`, `retry_after_ms` (default 250).

---

### `kv_threshold`

**Refuses when the KV cache is nearly full.**

| | |
|---|---|
| Decides on | KV cache utilisation |
| Needs | `kv_used_fraction` |
| Parameters | `threshold` (default 0.90) |
| Refuses with | `kv_pressure` |

**When it fits.** Long contexts and large batches, where memory genuinely fills
before anything else runs out.

**A warning, learned the hard way.** On a small model the KV cache is far
larger than a handful of short sequences can fill. We measured it never
exceeding **0.005** while the queue was 24 deep — the policy read a flat line
near zero and silently behaved as "accept everything", scoring identically to
the baseline while appearing to work. Check that your signal actually moves
before trusting a result. See [`../../../docs/results.md`](../../../docs/results.md).

---

### `chronos_inspired`

**Refuses requests it can prove will miss their deadline.**

| | |
|---|---|
| Decides on | a predicted worst-case wait, compared against *this* request's deadline |
| Needs | `running_requests`, `waiting_requests`, plus engine timing counters |
| Parameters | `chunk_tokens`, `safety_factor`, `window_s`, `fit_from_telemetry`, `defer_instead_of_reject` |
| Refuses with | `overloaded`, `deadline_unmeetable`, `tbt_unmeetable` |

The only policy here that reads the **request** rather than only the server.
Two requests arriving into identical conditions can get different answers,
because they asked for different things.

Three checks, in order: is the server oversubscribed; would this request's
predicted wait exceed its deadline; would one more response break the token
rhythm. The rejection reason records which check refused it.

**Source.** Marref, Tarmissi & Chaibi, *Frontiers in Computer Science* 8
(2026), [doi 10.3389/fcomp.2026.1873627](https://doi.org/10.3389/fcomp.2026.1873627).

**Why "inspired".** The paper's numbers come from a simulator with hardware
speeds calculated on paper. Ours are measured from a live server, and one of
them — the fixed per-step overhead — we could not measure at all. It runs their
algorithm; it does not reproduce their guarantee. Full write-up, including a
run where it was beaten by `queue_depth`:
[`../../../reports/chronos-reproduction.md`](../../../reports/chronos-reproduction.md).

Internally it is three separable pieces, because the first two are reusable by
any policy that predicts rather than thresholds:

| Module | Job |
|---|---|
| `chronos/estimator.py` | engine counters → current service rates, arrival-rate window |
| `chronos/wcrt.py` | the paper's formulas, pure arithmetic, no engine |
| `chronos/policy.py` | wires them together |

---

## Choosing one

| If the binding constraint is… | Use | Because |
|---|---|---|
| the concurrency cap | `queue_depth` | queue depth is what moves |
| KV memory | `kv_threshold` | cache pressure is what moves |
| meeting per-request deadlines | `chronos_inspired` | it is the only one that reads the deadline |
| nothing — you want a baseline | `no_admission` | it is the thing to beat |

A policy reading the wrong signal for its regime **does not fail loudly**. It
sees a flat line and quietly becomes "accept everything". Declaring `requires`
is what catches the case where the engine cannot supply the signal at all; it
cannot catch a signal that is present but never moves.

---

## Adding your own

Nothing needs to change in this repo — that is what the entry-point group is
for. In your own package:

```python
from admitperf import AdmissionPolicy, Decision, Request, SystemState


class MyPolicy(AdmissionPolicy):
    name = "my_policy"
    requires = frozenset({"waiting_requests"})

    def decide(self, req: Request, state: SystemState) -> Decision:
        if state.waiting_requests > 10:
            return Decision.reject(reason="queue_depth")
        return Decision.admit()
```

```toml
[project.entry-points."admitperf.policies"]
my_policy = "my_pkg:MyPolicy"
```

`pip install -e .` and it appears in `admitperf policies`. Conventions and the
fidelity rule for porting published algorithms are in
[`../../../CONTRIBUTING.md`](../../../CONTRIBUTING.md).
