# Archive — experiments that ran on Modal

These are **not runnable.** They provisioned a single GPU on Modal, and the Modal
provider has been removed: a hosted single-container provider cannot demonstrate
placement, a KV hop, or a multi-worker fleet, which is what this project measures.

They are kept for two reasons, and neither is nostalgia.

**The results are evidence.** `half-capacity-headroom/results/` and
`shedding-vs-tail-latency/results/` hold the bundles behind claims made in
`src/admitperf/docs/`. Deleting the configs beside them would leave figures whose
provenance could not be checked, which is the exact failure this project exists to
report. A number is traceable only if the config that produced it is still there.

**The prose records findings.** `half-capacity-headroom` is why signal liveness is
reported at all: three repeats, `waiting_requests` identically zero, and it was
treated as banked evidence for weeks. Its config is the artifact of that mistake.

## What replaces them

Experiments no longer describe infrastructure. A cluster config does that, and
declares which admission policy is active:

    infra/config/single.yaml      hosts, model, engine, topology, admission
    experiments/<name>/           cluster: <that file>, plus load and repeats

See `experiments/signal-liveness/` for the current shape.

## Reading an archived config

The `infra:` block in each file described a Modal deployment. The equivalent now
lives in `infra/config/base.yaml`, and the `policies:` list is replaced by the
cluster config's `admission:` block — one policy per deployment, because comparing
policies means comparing deployments that differ in nothing else.
