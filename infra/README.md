# infra — the serving cluster

Vendored from a course lab and rebuilt so the whole deployment comes from one
config. Excluded from this repository's lint, so that future diffs against upstream
stay readable.

```
config/      base.yaml + single.yaml, pair.yaml, disagg.yaml
render.py    config -> Kubernetes manifests + the gateway's environment
gateway/     guardrails, admission, placement, metrics
router/      pools, placement scoring, the KV bus
setup/       bring-up scripts, run over SSH
k8s-config/  static manifests: gateway, mooncake, observability, Open WebUI
observability/  ten Grafana dashboards, generated
docs/        how it all works -> docs/README.md
```

```bash
admitperf infra render infra/config/single.yaml --plan   # what would deploy
admitperf infra render infra/config/pair.yaml --env      # the gateway's env
```

**Documentation is in [`docs/`](docs/README.md).** Start with
[`docs/topology.md`](docs/topology.md) for why the config says what it says, and
[`docs/RUNBOOK.md`](docs/RUNBOOK.md) for bringing a cluster up by hand.
