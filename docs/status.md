# Status

*Updated: 2026-09-13*

## What exists today

| Component | Status | Location |
|---|---|---|
| **Adapter API v0** — `Decision`, `Request`, `SystemState`, `AdmissionPolicy` | ✅ Frozen 2026-09-11 | `src/admitperf/policies/base.py` |
| `TraceEvent` / `TraceLoader` ABCs | ✅ Defined | `src/admitperf/traces/base.py` |
| `NoAdmission` baseline policy | 🟡 Stub | `src/admitperf/policies/no_admission.py` |
| Policy registry + `get_policy()` | ✅ Working | `src/admitperf/policies/__init__.py` |
| CLI skeleton (`policies`, `traces`, `run`) | 🟡 `run` not implemented | `src/admitperf/cli.py` |
| Smoke tests (7 passing) | ✅ Green | `tests/test_smoke.py` |
| Harness / runner | ⛔ Empty | — |
| Metrics collector | ⛔ Empty | — |
| Trace loaders (Poisson, ShareGPT, agent) | ⛔ None | — |
| Reference policy ports (Chronos-inspired, QLM-inspired, etc.) | ⛔ None | — |
| Engine adapters (vLLM, SGLang) | ⛔ None | — |
| Results bundle, leaderboard | ⛔ None | — |

## Reality check against the architecture diagram

In [`architecture/03-component.png`](architecture/03-component.png), only the green **Adapter API** and **Policy registry** boxes plus the **NoAdmission (P0)** plugin actually exist (green fill). Everything shown dashed-grey is a specification, not code.

## Next milestone — Week 1 (in-flight)

- vLLM adapter shim reading `/metrics` and populating `SystemState`
- One Poisson-arrival synthetic workload with two SLO classes
- Results bundle v0 (SHA-256-signed run manifest)
- GitHub Actions smoke run of `NoAdmission` on 100 requests, printing goodput / admit% / reject%

Definition of done: someone watches a green CI run and reads a coherent `metrics.json`.

## Prior-art context

Prior-art audit, feasibility review, and adversarial critique of the framing are in [`prior-art/`](prior-art/). Read the adversarial review before writing any claim containing the word "first."
