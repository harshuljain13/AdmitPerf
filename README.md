<p align="center">
  <img src="assets/banner.svg" alt="AdmitPerf — standardized admission control for LLM inference" width="100%"/>
</p>

# AdmitPerf

*Standardized admission control for LLM inference.*

**Bring your own infra.** Your gateway already has raw metrics. AdmitPerf turns
them into signals, policies decide on signals, and every decision is recorded in
a form a report can compare across deployments.

It does not provision, serve, scrape, or generate load.

`admitperf.core` — the part that runs in your request path — imports nothing but
the standard library, opens no socket and spawns no subprocess. The CLI needs
`click`; core does not, and a test checks that by importing it with `click` blocked.

## Three things

| | |
|---|---|
| **Metrics** | whatever you scrape. Raw names, hundreds of keys, any stack. Yours. |
| **Signal** | a named quantity, and how to read it from your metrics. Ours, so `kv_pressure = 0.93` means the same thing in two deployments. |
| **Policy** | decides on signals. Four ship with AdmitPerf; yours is an equal citizen. |

## Use it

```python
from admitperf import Policy
from admitperf.core.signals import KV_PRESSURE, QUEUE_DEPTH


class KvWall(Policy):
    name = "kv_wall"

    def decide(self, metrics):
        if KV_PRESSURE.read(metrics) >= self.threshold:
            return self.reject("kv_pressure")
        if QUEUE_DEPTH.read(metrics) > self.max_waiting:
            return self.defer("queue_depth", retry_after_ms=200)
        return self.admit()
```

In your gateway:

```python
policy = KvWall(threshold=0.90, max_waiting=32, log="decisions.jsonl")

d = policy(raw_metrics, request_id=rid)  # whatever you scraped
if not d.admitted:
    return Response(d.status, retry_after=d.retry_after_ms)
```

`d.status` is derived from the reason — 503 for capacity, 429 for client-attributable
causes — so your refusals are comparable with anyone else's without you choosing a
code.

## Your metrics, whatever they are called

A signal tries its sources in order. Add yours and it goes first:

```python
from admitperf.core.signals import KV_PRESSURE

KV_PRESSURE.add_source("acme.cache.used_frac")  # your name
KV_PRESSURE.add_source(lambda m: m["blocks_used"] / m["blocks_total"])  # computed
```

Out of the box it already reads vLLM (`kv_cache_usage_perc`, and the older
`gpu_cache_usage_perc`, so a version difference is a non-event) and DCGM — including
the 0–100 to fraction conversion, because every host getting that wrong differently
is how a shared metric name stops meaning anything.

## Two rules it will not break

**Absence is never zero.** A signal nothing supplies reads `None`. A KV pressure of
`0.0` claims the cache is empty, which looks like headroom — so the policy would
admit everything while appearing to work.

**A value outside its range is skipped, not clamped.** Map a 0–100 metric to a
fraction and you get `None`, not a cluster that appears permanently saturated.

## What gets recorded

One JSON line per decision, holding **every** signal and the raw metrics — not just
the one your policy read. That is what lets a report say *"your signal never moved,
but queue depth hit 61"*, and what makes replaying a different policy over your own
production trace possible at all.

```python
policy.outcome(rid, ttft_ms=418, ok=True)  # optional; without it, no goodput
```

## Shadow mode, and a counterfactual

Call it and ignore the verdict: recording is unconditional and enforcement is yours,
so that is a complete shadow deployment with no flag.

```python
policy = KvWall(threshold=0.90, enforce=0.5)  # half the traffic governed
```

Both arms in one run under identical conditions, split deterministically by request
id so a retry is treated the same way twice. In production it caps the blast radius.

## Why

Across sixteen admission-primary papers surveyed, no two share a baseline, engine,
workload, or SLO definition — and **not one reports the observed range of the
quantity its policy reads**. So no reader can tell which published results describe
a policy acting and which describe a policy that never got the chance.

## Layout

```
src/admitperf/
  core/               runs in YOUR request path. Stdlib only, no sockets. One class per file.
    signal.py         Signal          signals.py    the signals we ship
    policy.py         Policy          decision.py   Decision
    verdict.py        Verdict         reasons.py    reason -> status code
    log.py            Log
  policies/           four baked-in policies, one per file
infra/                one worked example of a host. NOT part of the package.
docs/superseded/      the harness design this replaced, and why
```

`admitperf.core` imports nothing but the standard library, opens no socket, and
spawns no subprocess — `tests/test_layering.py` enforces each, because that is what
makes it safe to install in a gateway.

The CLI (`watch`, `report`, `dashboard`) lands next.

`make test` · `make lint`
