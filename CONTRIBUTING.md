# Contributing to AdmitPerf

The package is being rebuilt around a contract: metrics in, signals derived, policies
decide. Until that lands, the guidance that outlives it:

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

## Workflow

Branch off `main` as `feat/<short>`, `fix/<short>`, `docs/<short>`. Conventional
commits. Never commit to `main`. `make lint && make test` before pushing.

Open an issue with the `question` label if anything here is unclear.
