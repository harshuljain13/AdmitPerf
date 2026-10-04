# Contributing to AdmitPerf

Thanks for wanting to add a policy, a signal, or a report. This guide covers what
every contribution has to satisfy.

## 1. The contract

A policy decides on **signals**, which AdmitPerf derives from whatever raw metrics
your host exports:

```python
from admitperf import Policy
from admitperf.core.signals import KV_PRESSURE


class YourPolicy(Policy):
    name = "your_policy"

    def decide(self, metrics):
        if KV_PRESSURE.read(metrics) >= self.threshold:
            return self.reject("kv_pressure")
        return self.admit()
```

**Rules**

- `decide()` is a pure function of its input. No clock reads, no unseeded randomness.
  State across calls (AIMD counters, EWMA windows) lives on `self` and is exposed so a
  test can pin it.
- A refusal reason must come from `admitperf.core.reasons.REASONS`. Reports group by
  reason, and a reason nobody else uses cannot be grouped with anyone else's. The
  status code is derived from it, so a policy never picks one.
- `Signal.read()` returns `None` when nothing supplies the signal. Handle that — a
  policy that treats a missing KV fraction as `0.0` concludes the cache is empty,
  admits everything, and scores identically to no policy at all.
- Do not reach for a vendor metric name. If you need a quantity AdmitPerf does not
  define, read `metrics["your:metric"]` directly: it stays host-specific, which is
  honest, and still lands in the log.

### Adding a signal

A signal is a promise that two deployments reporting the same number mean the same
thing, so adding one to `core/signals.py` is a deliberate act — and a promise nobody
checks is worse than no entry. It needs a `help` string, a floor, and an upper bound
if it is a fraction, because a fraction without one cannot catch a source returning
percent.

Teaching an existing signal to read a new stack needs no change here:

```python
KV_PRESSURE.add_source("acme.cache.used_frac")
KV_PRESSURE.add_source(lambda m: m["blocks_used"] / m["blocks_total"])
```

## 2. The fidelity rule for reference-policy ports

If you are porting a published algorithm (Chronos, QLM, CONCUR, etc.), you must decide honestly whether it is a **faithful port** or an **ingress approximation**.

- ✅ `class ChronosWCRT` — implements the exact bound from the paper, cites the equation number.
- ✅ `class ChronosInspiredThreshold` — takes the *idea* but simplifies; class name says "inspired" not the paper's system name.
- ❌ Never name a class after a published system unless you can defend the port line-by-line against the paper. See [`docs/scope.md`](docs/superseded/scope.md#the-fidelity-rule-for-reference-ports).

Add a short docstring header linking the source paper and stating what was preserved vs simplified.

## 3. Tests

Every policy needs at least three tests in `tests/policies/test_<your_policy>.py`:

1. **Admits in the trivial case** — empty system, one request → `ADMIT`.
2. **Rejects at the boundary** — construct the state that should trigger reject, verify `Decision.kind is DecisionKind.REJECT` and `reason` is set.
3. **Determinism** — call `decide()` twice with the same inputs, assert equal outputs.

Run locally:

```bash
pytest tests/                             # all tests
pytest tests/policies/test_your_policy.py # just yours
pytest -k determinism                     # cross-policy determinism suite
```

Against the mock engine, no GPU needed:

```python
from admitperf.core.log import Log

with YourPolicy(threshold=0.9, log="decisions.jsonl") as p:
    for metrics in your_recorded_trace:
        p(metrics, request_id=next_id())

print(Log.read("decisions.jsonl"))
```

No cluster needed: a policy is a function of metrics, so a recorded trace exercises
it exactly as production would. AdmitPerf does not generate load — that is your
infra's job, and `vllm bench serve` already does it well.

## 4. Coding conventions

- **Python 3.11+** with type hints on every public function.
- **`ruff`** for formatting and linting — `ruff format . && ruff check .` before pushing.
- **Docstrings** on every public class and function; one line is fine if the name is clear.
- **No dependencies added lightly** — a new dependency requires a note in the PR explaining why the stdlib doesn't cover it.
- **Never `except Exception`** — catch the specific error class.
- **Never mutate frozen dataclasses** — construct new ones.

## PR flow

1. Fork or branch off `main`. Branch naming: `feat/<short>`, `fix/<short>`, `docs/<short>`.
2. One logical change per PR. If you are adding a policy, do not also refactor the harness.
3. Conventional commit prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.
4. PR description states: what the policy does, what paper it derives from (if any), and which of the three tests above you added.
5. Wait for green CI + one reviewer approval.

## What we do NOT accept

- Policies that require patching engine internals (see [`docs/scope.md`](docs/superseded/scope.md#class-b--batch-formation-not-portable)).
- Claims of a faithful port that we cannot verify against the source paper.
- New metrics without a definition in [`docs/metrics.md`](docs/superseded/metrics.md).
- Changes to the contract (`Policy`, `Decision`, `Signal`, `REASONS`) or to a signal's
  meaning — those are what make two deployments comparable. Open an issue first.
- Metrics estimated rather than measured. If a number cannot be obtained from the engine or from client-side timing, it belongs in the bundle's `unavailable` block with a reason.

## Questions

Open an issue with the `question` label, or read the [motivation](docs/superseded/motivation.md), [scope](docs/superseded/scope.md) and [status](docs/status.md) docs first.
