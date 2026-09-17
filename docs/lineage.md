# Lineage — where AdmitPerf comes from

AdmitPerf is not a greenfield build. Two prior artifacts inform its design.

## **a working two-replica vLLM gateway** (`module7-admission-and-routing` in a companion experiments repository)

A working admission gateway running against a live two-replica vLLM fleet on Lambda. Concept-map to AdmitPerf:

| module7 today | AdmitPerf |
|---|---|
| `gateway/admission.py::should_shed(fleet, req) -> str \| None` | `AdmissionPolicy.decide(req, state) -> Decision` |
| `gateway/state.py::FleetState` (Prometheus scrape) | `SystemState` |
| `PendingRequest` | `Request` |
| `bench/report.py` (366 LoC) → `results.json` / `results.html` | `bench/results.py` → results bundle |
| `gateway/scrape.py` Prometheus parsing | `engines/vllm.py` |
| `setup/launch_replicas.sh` vLLM flags | `infra/modal_app.py` |

`should_shed` **is AdmitPerf with N=1** — one hardcoded five-signal policy (`no_signal`, `kv_pressure`, `queue_depth`, `no_headroom`, `deadline_unmeetable`), with its signals welded to the state object and no way to swap the policy.

So the work is not "build a harness from scratch." It is: **lift that hardcoded policy out into a plugin slot, put enough policies in it for comparison to mean something, and add replay so a run is reproducible.**

This also settles the feasibility audit's largest risk — *"you will have to patch vLLM internals."* module7 demonstrates you do not: admission sits in front of the fleet, reading `/metrics`. Only preemption-loss accounting needs anything inside the engine.

## **the teaching version of the same function** (`module8-mini-serving-system/admit.py`)

The teaching version of the same function — a stub students implement as coursework. A stable AdmitPerf API is what that assignment could target next semester.

## Design principles carried forward

1. **Admission sits in front of the fleet**, reading engine `/metrics`. It does not modify engine internals.
2. **Signals are read-only**. Nothing the policy does mutates the engine state directly.
3. **The decision function is pure** — same inputs, same output, always. That is the reproducibility property.
4. **Rejection has an HTTP contract** — 429 / 503 / 529 carry different meanings (see [`design.md`](design.md)); the policy chooses.

## What came across verbatim

Two operational lessons were copied rather than rediscovered:

- **The vLLM metric names.** `engines/vllm.py` reads `vllm:kv_cache_usage_perc`, not the
  `gpu_cache_usage_perc` that older documentation describes — vLLM v1 renamed it, and the
  working gateway is what proved which name is current. A contract test now pins them
  against a captured payload so the next rename fails loudly instead of silently reading
  zero.
- **`--max-num-seqs 8`.** Carried into `infra/modal_app.py` as the default. It is the
  bottleneck that creates queueing, and without queueing every admission policy scores
  identically because the fleet never saturates.
