# Lineage — where AdmitPerf comes from

AdmitPerf is not a greenfield build. Two prior artifacts inform its design.

## `llm-inference-experiments/module6-admission-and-routing/`

A working admission gateway running against a live two-replica vLLM fleet on Lambda. Concept-map to AdmitPerf:

| module6 today | AdmitPerf |
|---|---|
| `gateway/admission.py::should_shed(fleet, req) -> str \| None` | `AdmissionPolicy.decide(req, state) -> Decision` |
| `gateway/state.py::FleetState` (Prometheus scrape) | `SystemState` |
| `PendingRequest` | `Request` |
| `bench/report.py` (366 LoC) → `results.json` / `results.html` | metrics collector → results bundle |

`should_shed` **is AdmitPerf with N=1** — one hardcoded five-signal policy (`no_signal`, `kv_pressure`, `queue_depth`, `no_headroom`, `deadline_unmeetable`), with its signals welded to the state object and no way to swap the policy.

So the work is not "build a harness from scratch." It is: **lift that hardcoded policy out into a plugin slot, put enough policies in it for comparison to mean something, and add replay so a run is reproducible.**

This also settles the feasibility audit's largest risk — *"you will have to patch vLLM internals."* module6 demonstrates you do not: admission sits in front of the fleet, reading `/metrics`. Only preemption-loss accounting needs anything inside the engine.

## `llm-inference-experiments/module7-mini-serving-system/admit.py`

The teaching version of the same function — a stub students implement as coursework. A stable AdmitPerf API is what that assignment could target next semester.

## Design principles carried forward

1. **Admission sits in front of the fleet**, reading engine `/metrics`. It does not modify engine internals.
2. **Signals are read-only**. Nothing the policy does mutates the engine state directly.
3. **The decision function is pure** — same inputs, same output, always. That is the reproducibility property.
4. **Rejection has an HTTP contract** — 429 / 503 / 529 carry different meanings (see [`design.md`](design.md)); the policy chooses.
