# Status

*Updated: 2026-09-14*

## What exists today

| Component | Status | Location |
|---|---|---|
| **Adapter API v0** — `Request`, `SystemState`, `Decision`, `AdmissionPolicy` | ✅ Frozen 2026-09-11 | `core/api.py` |
| Configuration — one definition of every setting, YAML + flag overrides | ✅ Working | `core/config.py` |
| Policy registry + third-party discovery via entry points | ✅ Working | `core/registry.py` |
| State cache with staleness accounting | ✅ Working | `core/state.py` |
| Runner — background scrape + arrival loop, staleness ceiling | ✅ Working | `core/runner.py` |
| **vLLM adapter** — `/metrics` scrape, streaming submit, client-side timing | ✅ **Verified on real hardware** | `engines/vllm.py` |
| **Modal provisioning** — deploy, session, teardown | ✅ **Verified on real hardware** | `infra/` |
| Poisson workload, three SLO classes | ✅ Working | `bench/workloads/poisson.py` |
| Experiment driver — every policy × repeats | ✅ Working | `bench/experiment.py` |
| Results bundle + provenance tags + unavailable metrics | ✅ Working | `bench/results.py` |
| Cross-run comparison with spread | ✅ Working | `bench/compare.py` |
| CLI — `infra up/status/smoke/down`, `bench run/compare/report` | ✅ Working | `cli.py` |
| Policies — `NoAdmission`, `KVThreshold`, `QueueDepth`, `QueueDepthDefer` | ✅ Working | `policies/` |
| Mock vLLM for GPU-free testing | ✅ Working | `scripts/mock_vllm.py` |
| Tests | ✅ 122 passing | `tests/` |
| SGLang adapter | ⛔ None | — |
| Lambda provider | ⛔ None — use `--engine-url` against a box you started | — |
| Chronos-inspired port + reproduction report | ✅ Working | `policies/chronos/` · [`reports/chronos-reproduction.md`](../reports/chronos-reproduction.md) |
| Other reference policy ports (QLM-inspired, …) | ⛔ None | — |
| Tenant-fairness and agent-session workloads | ⛔ None | — |
| HTML report — verdict, caveats, figures, provenance, one file | ✅ Working | `bench/report.py` |
| Dashboard — configure, run the pipeline, read results | ✅ Working | `dashboard/` |
| Runtime middleware (ASGI/Envoy) | ⛔ None | — |

## Verified end to end

A real deployment ran on 2026-09-14: Qwen2.5-0.5B on an A10G, three policies,
two repeats each. Results and caveats in [`results.md`](results.md).

That run found four defects no amount of faking would have surfaced:

1. **The container served the default model.** `infra/modal_app.py` is imported
   twice — locally by `modal deploy`, then again inside the container — and the
   config only existed for the first. Decorator arguments were correct;
   everything read inside the serve function fell back to defaults.
2. **The engine died at the first sampled token.** vLLM selects FlashInfer for
   top-k/top-p sampling, which JIT-compiles the kernel on first use and needs
   `nvcc`, absent from the slim image.
3. **Nothing ever queued inside vLLM.** A Modal container serves one input at a
   time unless told otherwise, so load queued at the proxy and the engine saw
   strictly sequential traffic. `num_requests_running` sat at 1 and
   `num_requests_waiting` at 0 while latency climbed — the admission signal was
   flat for a reason that had nothing to do with admission.
4. **Teardown silently failed.** `modal app stop` prompts for confirmation and
   aborts without a terminal, leaving the deployment running and billing.

All four are fixed. The third is the one worth remembering: the benchmark can
be wired correctly end to end and still measure the platform instead of the
engine.

## The honest gaps

- **One model, one GPU, one workload.** Nothing here generalises yet.
- **No reference policy ports.** `QueueDepth` is a threshold, not a port of a
  published algorithm. The roster in [`policies.md`](policies.md) is unbuilt, so
  there is no head-to-head against prior work — which is the actual goal.
- **The report is only as good as the runs behind it.** `bench report` writes
  a standalone `report.html` with figures and provenance, and it states its
  own caveats — but a gap inside the run-to-run spread is still a gap inside
  the spread, however well it is rendered.
- **Two repeats is thin.** Enough to see that the baseline's spread is large,
  not enough to defend a small difference between policies.

## Metric availability

Two metrics named in [`metrics.md`](metrics.md) cannot be produced, and appear
in every bundle's `unavailable` block with the reason rather than estimated:

- **preemption loss in KV bytes** — no engine exposes it;
  `vllm:num_preemptions_total` is a count, not a volume.
- **GPU utilization** — needs DCGM alongside the engine; not collected.

Runs also record `signal_was_healthy`. A run where most scrapes failed produces
numbers that look ordinary and describe nothing, so it is flagged rather than
left for a reader to notice.

## What was removed, and why

An earlier iteration carried a simulated engine, an injectable clock, and a
defer queue — roughly a third of the codebase — deleted in `7a95fb7`. A
simulated engine cannot answer whether one policy beats another on a real
serving stack. The clock existed only to make that simulation deterministic,
and the defer queue only because `DEFER` is in the frozen API; against a live
engine, deferring is a sleep and a second question.

They remain in git history if a deterministic CI substrate is ever wanted.
