# Contributing to AdmitPerf

The contract: metrics in, signals derived, policies decide. A policy implements one
method,

```python
def decide(self, metrics: Mapping[str, float]) -> Decision
```

and the base class records every decision. Everything below protects that contract.

## What AdmitPerf is not

Not a gateway, not a provisioner, not a load generator, not an engine adapter. It
installs into someone else's request path and must know nothing about their stack.
A change that teaches the package about vLLM, Kubernetes, or a cloud provider is a
change in the wrong direction.

## The fidelity rule for reference-policy ports

Never name a class after a published system unless the port can be defended
line-by-line against the paper. Ship the *mechanism*, never the paper's thresholds —
those were tuned on other hardware, and parameter provenance is one of the seven
reporting items.

A policy that cannot be implemented behind an ingress hook — one deciding at batch
formation — should be documented as such rather than approximated under the paper's
name. Six of the sixteen surveyed policies are in that category.

## Rules that are tested, not trusted

- The package may not import `infra`, name an infra path, or ship it in the wheel.
- `admitperf.core` imports nothing but the standard library, opens no socket, and
  spawns no subprocess. It runs in a production request path.
- One class per file.

`tests/test_layering.py` enforces each. It previously resolved its source path to a
directory that did not exist, so it globbed zero files and stayed green throughout the
period those rules were being broken — which is why it now asserts its own path first.

## Adding a policy or a signal

- A policy subclasses `admitperf.Policy`, declares `name` and the signal it reads
  (`signal_quantity`, `signal_structure`, `slo_awareness`), and reads metrics only
  through signals. `.claude/skills/admitperf-policy/SKILL.md` walks through a port.
- Its tests must show it refusing *something* under pressure. A policy that admits
  everything passes most tests.
- A new metric name for an existing signal is `add_source`, not a new signal.

## Workflow

Branch off `main` as `feat/<short>`, `fix/<short>`, `docs/<short>`. Never commit to
`main`. One logical change per PR; a change that depends on another goes in a separate PR
based on it. Titles are conventional commits (`feat(policies):`, `fix(core):`, `docs:`,
`ci:`), because release notes are built from them.

Before pushing, in a fresh virtual environment with `pip install -e '.[all]'` (what CI
installs):

```bash
ruff check . && ruff format --check . && pytest -q
```

Check the exit code, not the last line of output.

The PR body says what changed and why, how you verified it (what you ran and saw, not
"tests pass"), and what you left undone. Any number in a doc names the run it came from.

Never edit the version: releases are cut by pushing a tag.

Open an issue with the `question` label if anything here is unclear.
