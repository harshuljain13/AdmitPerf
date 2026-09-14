# Status

*Updated: 2026-09-14*

## What exists today

| Component | Status | Location |
|---|---|---|
| **Adapter API v0** — `Request`, `SystemState`, `Decision`, `AdmissionPolicy` | ✅ Frozen 2026-09-11 | `core/api.py` |
| `RequestOutcome`, `DecisionRecord`, `EngineAdapter` | ✅ Working | `core/ports.py` |
| Policy registry + third-party discovery via entry points | ✅ Working | `core/registry.py` |
| State cache with staleness accounting | ✅ Working | `core/state.py` |
| Runner — background scrape + arrival loop | ✅ Working | `core/runner.py` |
| **vLLM adapter** — `/metrics` scrape, streaming submit, client-side timing | ✅ Working | `engines/vllm.py` |
| Poisson workload, three SLO classes | ✅ Working | `bench/workloads/poisson.py` |
| Results bundle + summary with provenance tags | ✅ Working | `bench/results.py` |
| CLI — `infra up/status/down`, `smoke`, `run`, `policies` | ✅ Working | `cli.py` |
| Modal provisioning | 🟡 **Written, never run against real Modal** | `infra/modal_provider.py` |
| Policies — `NoAdmission`, `KVThreshold` | ✅ Working | `policies/` |
| Fake vLLM for GPU-free testing | ✅ Working | `scripts/fake_vllm.py` |
| Tests | ✅ 78 passing | `tests/` |
| SGLang adapter | ⛔ None | — |
| Reference policy ports (Chronos-inspired, QLM-inspired, …) | ⛔ None | — |
| Tenant-fairness and agent-session workloads | ⛔ None | — |
| Leaderboard across policies | ⛔ None | — |
| Runtime middleware (ASGI/Envoy) | ⛔ None | — |

## The honest gap

**No number has yet come off real hardware.** Everything above has been exercised end
to end against `scripts/fake_vllm.py`, which proves the wiring but measures nothing about
GPUs. The Modal provisioner is tested against a faked `modal` CLI, so URL parsing and
failure handling are covered, but no deploy has actually happened.

The first real `infra up` is the next milestone, and is likely to surface something: the
`huggingface` secret reference in `modal_app.py` assumes a Modal secret by that name, the
GPU string may need adjusting, and a cold weight download may exceed the startup timeout.

## What was removed, and why

An earlier iteration carried a simulated engine (`ReplayEngine`), an injectable clock, and
a defer queue — roughly a third of the codebase. All three were deleted in `7a95fb7`.

A simulated engine cannot answer the question the project exists to ask, which is whether
one policy beats another on a real serving stack. The clock existed only to make that
simulation deterministic, and the defer queue only because `DEFER` is in the frozen API —
against a live engine, deferring is a sleep and a second question, not a data structure.

They remain in git history if a deterministic CI substrate is ever wanted.

## Metric availability

Two metrics named in [`metrics.md`](metrics.md) cannot be produced and are reported as
`unavailable` in every bundle, with the reason, rather than estimated:

- **preemption loss in KV bytes** — no engine exposes it. `vllm:num_preemptions_total` is
  a count, not a volume.
- **GPU utilization** — needs DCGM running alongside the engine; not collected.
