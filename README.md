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
workload, or SLO definition — and **not one reports the observed range of the quantity
its policy reads**. So no reader can tell which published results describe a policy
acting and which describe a policy that never got the chance.

The arithmetic matters as much as the literature. On one A100-40GB serving
Qwen2.5-7B, the KV pool holds roughly 384k tokens. At `max_num_seqs=64` with a
2,168-token request, resident tokens cap near 139k — so `kv_used_fraction` cannot
exceed about **0.35**, and a policy thresholded at 0.90 is unreachable at any arrival
rate. A run like that reports numbers indistinguishable from no policy at all.

That is the question AdmitPerf answers first: **could the policy have fired at all?**

## What it gets you

The whole point: two logs, one without admission control and one with.

```bash
python examples/compare_two_policies.py          # no GPU, no cluster, 2 seconds
```

```
  The policy refused 240 of 400 requests (60.0%).

  p95 TTFT of served requests is 15.17x lower: 2730ms -> 180ms
  goodput is DOWN: 0.480 -> 0.400  (of offered)

  Goodput fell, so the policy refused requests the cluster could have
  served. Faster tails bought at that price are not a win.
```

A 15× better tail, and the policy is **worse**. A KV threshold picked without
reference to the SLO sheds requests the cluster could still have served in time. The
same comparison against a queue bound derived from the SLO:

```
  The policy refused 110 of 400 requests (27.5%).

  p95 TTFT of served requests is 1.67x lower: 2730ms -> 1630ms
  goodput is up: 0.480 -> 0.670  (of offered)
```

A report showing only latency would have picked the first policy. That is the argument
for comparing, and `goodput` divides by requests **offered** rather than admitted —
divide by admitted and refusing 95% of traffic reads as 1.00.

Three checks come before any of those numbers: the policy fired at all, both runs
faced the same load, and outcomes were recorded. `admitperf compare --check` exits
non-zero when any fails.

## The CLI

Three verbs, and none of them is in a request path.

```bash
admitperf signals                                   # what can your stack already feed?
admitperf watch http://host:8000/metrics --for 1h   # record it, no code change
admitperf report trace.jsonl                        # the finding
admitperf compare baseline.jsonl with-policy.jsonl  # what did it buy?
admitperf dashboard                                 # every log, in a browser
```

Start with `watch`. It answers *could a policy have fired here, and which signal
actually moved* before you touch your gateway:

```
THE FINDING — UNKNOWN  (3600 state samples)

  signal                  min      p50      p95      max   n
  kv_pressure            0.07     0.21     0.44     0.44  3600
  queue_depth               0        3       47       61  3600
  gpu_util               0.11     0.88     0.96     0.99  3600
  prefix_hit_rate          --   never supplied by your metrics
```

KV pressure never passed 0.44, so a policy thresholded at 0.90 could not have fired
no matter how it was written. Queue depth hit 61. That is the finding, and it cost no
integration.

`admitperf report --check` exits non-zero unless the log can support a claim, which
makes it usable in CI.

## On a real fleet

Nothing here provisions or drives load — that is your infra's job. AdmitPerf's part
is the last two commands.

```bash
# --- on the GPU box: your infra, your tooling ---
bash infra/setup/lambda_vllm.sh                  # serves Qwen2.5-7B on :8000

# --- from your laptop ---
ssh -L 8000:127.0.0.1:8000 -N ubuntu@$HOST       # Lambda allows SSH only

admitperf watch http://127.0.0.1:8000/metrics --for 10m -o trace.jsonl &

vllm bench serve --base-url http://127.0.0.1:8000 \
    --model Qwen/Qwen2.5-7B-Instruct \
    --random-input-len 8192 --random-output-len 512 --ignore-eos \
    --num-prompts 150 --request-rate 5

admitperf report trace.jsonl
```

**`--ignore-eos` is not optional.** Without it the model stops whenever it stops, and
on a filler prompt that can be 40 tokens instead of 512. The token count barely moves,
but *duration* collapses by an order of magnitude — and concurrency is rate × duration,
so the cache never fills and the whole run comes back inert for the wrong reason.

**8192 input, not 2048.** From the arithmetic above: at `max_num_seqs=64` a short
request caps `kv_used_fraction` near 0.35. Requests need to be longer than about
**6k tokens** before KV binds before the scheduler does, and only then can a
KV-pressure policy fire at all.

**On real hardware: not yet measured.** The figures above come from the simulated
engine in `examples/`, which is honest about what it models — bounded slots, a FIFO
queue, and KV pressure driven by resident tokens — and is checked by a test so the
numbers in this README cannot go stale silently. Earlier figures from real runs were
removed rather than carried forward, because they could not be reproduced.

## Layout

```
src/admitperf/
  core/               runs in YOUR request path. Stdlib only, no sockets. One class per file.
    signal.py         Signal          signals.py    the signals we ship
    policy.py         Policy          decision.py   Decision
    verdict.py        Verdict         reasons.py    reason -> status code
    log.py            Log
  policies/           four baked-in policies, one per file
  watch.py            Watch           report.py     Report
  comparison.py       Comparison      cli.py        the CLI
  dashboard/          the Streamlit reader
examples/             a runnable comparison, no hardware needed
infra/                one worked example of a host. NOT part of the package.
```

`admitperf.core` imports nothing but the standard library, opens no socket, and
spawns no subprocess — `tests/test_layering.py` enforces each, because that is what
makes it safe to install in a gateway.

`make test` · `make lint`
