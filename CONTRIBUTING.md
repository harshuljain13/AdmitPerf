# Contributing to AdmitPerf

Thanks for wanting to add a policy, workload, engine adapter, or metric. This guide covers the four things every contribution needs to satisfy.

## 1. The adapter API contract

Every policy implements a single interface:

```python
from admitperf import AdmissionPolicy, Decision, Request, SystemState

class YourPolicy(AdmissionPolicy):
    name = "your_policy"          # unique across the registry

    def decide(self, req: Request, state: SystemState) -> Decision:
        # pure function: same (req, state) -> same Decision, always
        ...
        return Decision.admit()               # or
        return Decision.defer(retry_after_ms=50, reason="queue_full")   # or
        return Decision.reject(reason="kv_pressure")   # 429 to the client
```

**Rules**:
- `decide()` MUST be a pure function of its inputs. No hidden state, no clock reads, no random numbers without a seed.
- If your policy needs state across calls (AIMD counters, EWMA windows), keep it on `self` and expose the fields so tests can pin them.
- Do not mutate `req` or `state`. Both are frozen dataclasses.

Register the policy in `src/admitperf/policies/__init__.py`:

```python
from admitperf.policies.your_policy import YourPolicy

POLICIES: dict[str, type[AdmissionPolicy]] = {
    NoAdmission.name: NoAdmission,
    YourPolicy.name: YourPolicy,   # add here
}
```

## 2. The fidelity rule for reference-policy ports

If you are porting a published algorithm (Chronos, QLM, CONCUR, etc.), you must decide honestly whether it is a **faithful port** or an **ingress approximation**.

- ✅ `class ChronosWCRT` — implements the exact bound from the paper, cites the equation number.
- ✅ `class ChronosInspiredThreshold` — takes the *idea* but simplifies; class name says "inspired" not the paper's system name.
- ❌ Never name a class after a published system unless you can defend the port line-by-line against the paper. See [`docs/scope.md`](docs/scope.md#the-fidelity-rule-for-reference-ports).

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

CI runs the smoke suite on every PR (~5 seconds, no GPU).

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

- Policies that require patching engine internals (see [`docs/scope.md`](docs/scope.md#class-b--batch-formation-not-portable)).
- Claims of a faithful port that we cannot verify against the source paper.
- New metrics without a definition in [`docs/metrics.md`](docs/metrics.md).
- Changes to the adapter API (`Request`, `SystemState`, `Decision`, `AdmissionPolicy`) — that is v0 frozen. If you think the API needs to change, open an issue first.

## Questions

Open an issue with the `question` label, or read the [motivation](docs/motivation.md) and [scope](docs/scope.md) docs first.
