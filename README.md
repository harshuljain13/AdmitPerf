<p align="center">
  <img src="assets/banner.svg" alt="AdmitPerf — standardized admission control for LLM inference" width="100%"/>
</p>

# AdmitPerf

*Standardized admission control for LLM inference.*

**Bring your own infra.** Your gateway already has raw metrics. AdmitPerf turns them
into signals, policies decide on signals, and every decision is recorded in a form a
report can compare across deployments.

It does not provision, serve, scrape, or generate load. A real gateway does auth,
rate limiting, routing and retries — admission is one concern inside it, and a
library that tries to *be* the gateway competes with the host's architecture.

## Status

**Being rebuilt, in the open.** This commit removes the benchmark harness the package
grew around — engine adapters, a provisioner, two run paths — so that the contract
replacing it is not built next to them. The contract and the CLI land in the PRs
stacked on this one.

**Measured results: none.** Earlier figures were removed rather than carried forward,
because they could not be reproduced.

What exists today: `infra/`, one worked example of a host — cluster configs, a
renderer with real refusals (fp8 on an sm80 card, weights leaving no room for KV,
tensor parallelism split across hosts), and the bring-up scripts. It is deliberately
not part of the package.

## Why

Across sixteen admission-primary papers surveyed, no two share a baseline, engine,
workload, or SLO definition — and **not one reports the observed range of the quantity
its policy reads**. So no reader can tell which published results describe a policy
acting and which describe a policy that never got the chance.

The arithmetic matters as much as the literature. On one A100-40GB serving
Qwen2.5-7B, the KV pool holds roughly 384k tokens. At `max_num_seqs=64` with a
2,168-token request, resident tokens cap near 139k — so `kv_used_fraction` cannot
exceed about **0.35**, and a policy thresholded at 0.90 is unreachable at any arrival
rate. A run like that reports numbers indistinguishable from no policy at all, and
nothing in the literature would tell you.

That is the question AdmitPerf answers first: **could the policy have fired at all?**

## Layout

```
src/admitperf/        the package
infra/                one worked example of a host. NOT part of the package.
tests/                boundaries enforced, not intended
```

`make test` · `make lint`
