# infra-2 — the cluster

One Helm chart. A topology is a **values file**, not a script.

```
make plan                   what would deploy, and whether a run on it can mean anything
make up                     deploy it
make up VALUES=disagg       a different topology
make down                   remove it
```

`make` on its own lists everything.

---

## Why this replaces `infra/`

`infra/` had three things each half-owning deployment: fifteen ordered shell scripts,
static manifests with values baked in, and a Python renderer generating some of the
same resources. They disagreed, and the disagreements were invisible:

| what happened | why |
|---|---|
| `lambda_vllm.sh` served at `gpu-memory-utilization 0.85` while the config said `0.90`, and omitted `--max-num-seqs` entirely | the flag lived in two files |
| `mooncake-store` deployed onto a one-GPU box with no second engine to hand KV to | nothing tied the transport to the topology needing it |
| HAMi and KEDA installed on a config that declared neither | the bring-up script had no view of the config |
| the gateway came up healthy pointing at `http://vllm-prefill:8000` on an aggregated run | the manifest hardcoded a disaggregated topology |
| every request returned `400 prompt_too_long` | the guardrail's 8,000-**character** default, against a ~33,000-character prompt |
| 28 of 32 requests returned `429 tenant_tokens` | a 10,000 tokens/min default — under **two** requests at experiment length |

None of those crash. They produce a cluster that looks healthy and numbers that mean
nothing, which is the expensive kind of bug.

In this tree every one of them is either computed from the values or **refused by
`helm template`**, which runs on a laptop in a second:

```
$ make lint
refusals:
  a pool name the gateway cannot address       refused
  a KV transport with no handoff               refused
  autoscaling without KEDA                     refused
  slicing without the HAMi scheduler           refused
  a chat UI bypassing the gateway              refused
guarantees:
  aggregated gateway points at its engine      ok
  guardrail cannot clip the prompt             ok
  no API key is rendered into a manifest       ok
```

**Nothing was dropped.** `lib/` is the lab's Python moved across byte-for-byte —
gateway, router, KV transports, overflow, kernels, dashboards. All eleven Grafana
dashboards ship as a ConfigMap.

---

## Layout

```
chart/
  Chart.yaml            HAMi, KEDA and kube-prometheus-stack as conditional subcharts,
                        so a topology that wants none installs none
  values.yaml           every default, with the reason next to it
  templates/
    _helpers.tpl        shared arithmetic: pool URLs, KV bytes/token, prompt ceiling
    _validate.tpl       the refusals above
    engine.yaml         loops .Values.pools — this replaces the renderer
    gateway.yaml        the whole environment computed from values
    mooncake.yaml       if kv.enabled
    autoscale.yaml      if autoscale.enabled
    gie.yaml            InferencePool + endpoint picker, if gie.enabled
    ui.yaml / dcgm.yaml / dashboards.yaml
  files/dashboards/     the eleven JSONs, from lib/observability/dashboards.py

values/
  single.yaml           1 GPU, one aggregated engine
  pair.yaml             2 GPUs, placement becomes a decision
  disagg.yaml           prefill + decode pools, 4 GPUs
  sliced.yaml           prefill + decode on HAMi slices of ONE card

lib/                    the lab's Python, unchanged
scripts/
  _env.sh               resolves the box from .env and the topology from VALUES
  bootstrap.sh          k3s + helm, once per machine
  sync.sh               chart + lib to the box
  up.sh                 bootstrap, sync, secrets, helm, tunnel
  tunnel.sh             forwards every NodePort and verifies WHAT ANSWERS
  down.sh / logs.sh
  check-refusals.sh     what `make lint` runs
```

---

## The topologies

| | GPUs | pools | KV transport | what it proves |
|---|---|---|---|---|
| `single` | 1 | `engine` | — | bring-up, gateway reaches an engine, decisions logged, report produced |
| `pair` | 2 | `engine` ×2 | — | placement: two destinations, so choosing is a decision |
| `disagg` | 4 | `prefill`, `decode` | mooncake | a long prefill cannot stall decode for everyone |
| `sliced` | **1** | `prefill`, `decode` | mooncake | disaggregation **plumbing** on one card, via HAMi slices |

**Pool names are not free.** `lib/gateway` and `lib/router` are written against exactly
`{prefill, decode}` in ~15 places — `metrics.py` keys its dicts on them, `pools.py`
branches on `phase in ("prefill", "both")`. A third name is silently coerced into one of
them, so the chart refuses it rather than deploying a pool the gateway cannot address.

---

## Secrets

`.env` holds `LAMBDA_HOST_1..N`, `LAMBDA_SSH_KEY`, `HF_TOKEN`, `OVERFLOW_API_KEY`. It is
excluded from the sync. `up.sh` turns the tokens into Kubernetes Secrets and the
templates reference them with `secretKeyRef` — so no key is ever rendered into a
manifest, where it would show up in `helm get values`, in CI logs, and in git.

---

## Known gap

**The gateway does not call AdmitPerf.** `lib/gateway/admission.py` and
`lib/router/router.py` carry their own admission logic — a tenant token bucket and a
`KV_SATURATION` check that sheds `kv_free`. They import nothing from `admitperf`.

`values.yaml` sets `gateway.admission.policy` and the chart puts it in the pod's
environment, where **nothing reads it**. That is stated rather than hidden: the thing
currently deciding admission is not the thing AdmitPerf is meant to measure.

Closing it means replacing those two code paths with `policy(metrics)`, or standing up
a Gateway API Inference Extension endpoint picker that calls AdmitPerf — `gie.enabled`
renders the `InferencePool` for it already.
