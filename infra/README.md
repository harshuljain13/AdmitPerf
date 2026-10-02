# Cluster documentation

| Page | Read it for |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The overview and component map |
| [DATAFLOW.md](DATAFLOW.md) | One request through the code, file by file |
| [gateway.md](gateway.md) | Entry, guardrails, admission |
| [router.md](router.md) | Placement, scoring, shedding |
| [kvbus.md](kvbus.md) | Prefix bookkeeping and the KV hop |
| [observability.md](observability.md) | Metrics, profiling, dashboards |
| [kernels.md](kernels.md) | Attention math, KV eviction, warmup, transports |
| [infrastructure.md](infrastructure.md) | Access, startup, HAMi, KEDA |
| [COMMANDS.md](COMMANDS.md) | Debugging commands |

The lab steps themselves are in the [module README](RUNBOOK.md).

**New here?** [DATAFLOW.md](DATAFLOW.md) answers "where does a request go" better
than the architecture overview does.

## Provenance

This cluster was vendored from a course lab — two vLLM workers with a KV hop, HAMi GPU
slicing, KEDA, and ten Grafana dashboards. It is kept as a working deployment to measure
admission policies against, and is excluded from this repository's lint so that future
diffs against upstream stay readable.

The dashboards carry our own names because they are generated from
`infra/observability/dashboards.py` and have diverged from the originals.
