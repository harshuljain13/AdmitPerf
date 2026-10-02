# Runbook — bringing the cluster up

The whole stack in one module: the disaggregated cluster from the cluster, the
observability layer from 9b, and a new engine-tuning layer on top.

| Layer | What you get | GPU |
|---|---|---|
| Engine tuning | Attention HBM math, KV eviction policies, warmup budget | **no** |
| Serving | Guardrails, admission, placement, prefill/decode, KV hop, burst | yes |
| Observability | Per-replica metrics, stage histograms, ten dashboards, load harnesses | yes |

Docs live in **[docs/](README.md)**: [DATAFLOW.md](DATAFLOW.md) walks
one request through the code file by file, [ARCHITECTURE.md](ARCHITECTURE.md)
is the overview and component map, [kernels.md](kernels.md) covers the
engine-tuning tools, [COMMANDS.md](COMMANDS.md) is the debugging reference.

**Everything runs through one SSH tunnel.** Lambda's firewall allows only SSH,
so `setup/ssh.sh` forwards seven ports. Leave the Step 2 session open for the
whole lab, and open only `127.0.0.1` URLs — never the public Lambda IP.

| URL | What | Available after |
|---|---|---|
| http://127.0.0.1:30030 | Open WebUI — no login | Step 4 |
| http://127.0.0.1:8080 | orch-serve gateway — Locust `--host` and the REPL | Step 4 |
| http://127.0.0.1:31495 | Grafana — user `admin` | Step 22 |
| http://127.0.0.1:8089 | Locust's own UI, not the gateway | Step 26 |

Spin up a Lambda instance and add it to your `.env` first.

---

## Laptop — kernels, eviction, warmup

No GPU, no cluster. These four commands are the layer the cluster adds.

```
cd module10-kernels-and-eviction
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
```

Set up an isolated environment. vLLM is not installed here — it goes on the GPU
box only.

```
pytest tests/
```

Twenty-two test files covering the router, guardrails, dashboards, kernels,
eviction and warmup. Expect green; `test_vllm_worker` skips without a live engine.

```
python -m kernels.engineering --q 128 --kv 2048
```

Counts HBM bytes for one attention call: naive vs FlashAttention vs paged.
Expect `naive/flash≈1.94`. Re-run with `--q 1` and the ratio collapses to ~1 —
**FlashAttention is a prefill optimisation**, it does little for decode.

```
python -m router.kv_eviction --policy prefix_protect --need 1024
```

Frees 1024 tokens by choosing victims. `prefix_protect` drops `old-unique` and
keeps `shared`, because a shared prefix costs every reusing request when evicted
while a unique one costs exactly one. Try `lru`, `lfu`, `priority` to compare.

```
python -m router.warmup --budget 1500 --prefix 256
```

Warmup cost vs first-request latency. Expect `speedup 32.28x` — that is **startup
time, not inference speed**. Capturing every CUDA-graph bucket gives a 40 ms first
TTFT for 18 400 ms of warmup; one bucket is ready 32x sooner and costs the first
user 1790 ms.

```
python -m gateway.harness --list
python -m gateway.harness --behavior fleet-soak
```

Ten named scenarios that drive the real gateway and router against simulated
workers, then assert on the metrics. `fleet-soak` expects
`completed=3 shed_503=17` — **heavy shedding is the pass condition**, proving the
deadline filter sheds rather than queueing forever.

```
python -m app.harness --list
```

Six guardrail scenarios — the 400s that happen before admission ever runs.

> If any of these fail with `No module named kernels.engineering`, set
> `PYTHONPATH=$PWD` (the `Makefile` does this for you; a bare shell does not).

---

## Lambda cluster

### Step 1

Mac terminal, already in `class-code/admitperf`.

```
bash setup/sync_to_lambda.sh
```

Copy the lab to `~/admitperf` on the GPU box over rsync/SSH. Reads `LAMBDA` and
`LAMBDA_SSH_KEY` from `.env`, so that file must exist first.

### Step 2

Same Mac terminal. This is the only SSH. Leave it open for the rest of the lab.

```
bash setup/ssh.sh
```

Opens the shell *and* the seven port-forwards. Every `127.0.0.1` URL in this
README works only while this session is alive — if a page stops loading, check
here first.

### Step 3

Same terminal — prompt is now `ubuntu@…:~/admitperf$`. Do not open another terminal.

```
bash setup/lambda_setup.sh
```

Build the venv, install requirements plus vLLM, verify torch and CUDA see the device.

### Step 4

Same SSH terminal.

```
bash setup/lambda_cluster.sh
```

k3s + HAMi, then the sliced workloads, Open WebUI, and the KEDA ScaledObjects.
This is the longest step.

### Step 5

Same SSH terminal.

```
kubectl get deploy,svc,scaledobject
```

Confirm what runs, how it is reached, and what will resize it. If anything is
not Ready, see **[docs/COMMANDS.md](COMMANDS.md)**.

### Step 6

Same SSH terminal.

```
bash setup/smoke_sliced.sh
```

Curl both model endpoints and check `nvidia-smi` shows two compute processes on
one GPU. Expect `SLICED SMOKE PASS`.

---

## Chat in the browser

### Step 7

New Mac terminal (local). Do not SSH. Leave the Step 2 SSH terminal open.

```
open http://127.0.0.1:30030
```

Open WebUI — no username or password. If the page does not load, Step 2 is not up.

### Step 8

Same browser tab. In the model picker choose **text** (or **vision**). Send one message and wait for a reply.

The UI is configured with `OPENAI_API_BASE_URL=http://orch-serve:8080/v1`, so it
talks to your gateway *inside* the cluster — the same admission and routing path
the REPL uses, not the vLLM pods directly.

---

## Send traffic from the REPL

### Step 9

Same Mac terminal as Step 7.

```
cd class-code/admitperf
```

### Step 10

Same Mac terminal as Step 7.

```
source .venv/bin/activate
```

### Step 11

Same Mac terminal as Step 7.

```
set -a && source .env && set +a
```

Export `.env` into this shell. This is what points the REPL at the tunnelled
cluster rather than at nothing.

### Step 12

Same Mac terminal as Step 7. This starts the REPL — stay here through Step 20.

```
python -m gateway.repl
```

The 10 REPL, replacing the cluster's `app.py`. Beyond the commands below:
`status`, `board`, `codes`, `plan`, `saturate`, `slice`, `force`, `flood`,
`cap`, `split`, `replicas`, `reset`, `help`.

Wait for the prompt. Type **one line**, then wait for `ROUTE` and `TEXT`.

### Step 13

Same REPL. Do not open another terminal.

```
text Write one sentence about a GPU.
```

Routes to the text model. Watch `ROUTE` name the pod that was picked.

### Step 14

Same REPL.

```
vision What color is this?
```

Routes to the vision model on the other slice. The REPL attaches a 1x1 PNG so
the vision guardrail is satisfied.

### Step 15

Same REPL.

```
audio Transcribe: hello from this lab.
```

**There is no audio pool.** Placement fails 503 and Overflow sends it to the
burst provider — the one step that deliberately spends money.

### Step 16

Same REPL. This is a prefill → decode KV hop. Wait for `HANDOFF` — `kv_hop` must not be `None`. Grafana Mooncake hops / blocks should increment.

```
hop Write one sentence about a GPU.
```

Forces the two-phase path, so the KV blocks actually transit Mooncake. Steps
13-15 are single-pick capability routing and produce no hop.

### Step 17

Same REPL. Drops the hopped prefix so the router cannot stick to a ghost cache.

```
evict
```

Bumps `orch_kv_evict_total`. Without this the router keeps scoring a prefix that
no longer lives anywhere.

Same REPL, still Step 17. The engine-tuning commands, now against the live fleet:

```
kernel 128 2048
pressure 1024 prefix_protect
warmup 1500
warmup seed
```

`kernel` prints the HBM comparison for that shape. `pressure` evicts to free
1024 tokens using `prefix_protect`, against the **real** `KVBus` this time, so
`orch_kv_evict_total` and the Mooncake dashboard both move. `warmup` costs a
1500 ms budget; `warmup seed` pins prefixes onto the bus first, which is what a
newly scaled replica would do to arrive useful rather than cold.

Full explanation of all three in **[docs/kernels.md](kernels.md)**.

### Step 18

Same REPL.

```
metrics
```

Dump the Prometheus text `gateway.metrics.METRICS` is serving — the same bytes
Grafana scrapes.

### Step 19

Same REPL.

```
profile
```

**The point of 10.** Latency broken down by stage — `gateway`, `pick`, `local`,
`overflow`, `e2e` — with count, total and mean. This is how you find which layer
is actually costing you.

### Step 20

Same REPL. After this you are back at the shell.

```
quit
```

### Step 21

Same Mac terminal as Step 7, now a normal shell again.

```
tail -n 5 traces/requests.jsonl
```

One JSON line per request. Read `happened` for the outcome verb and
`local_ms`/`overflow_ms` for what leaving cost.

---

## Day 2 — Grafana

### Step 22

Step 2 SSH terminal (already on the GPU box). Do not SSH again. Cluster from Step 4 must be up.

```
bash setup/day2_observability.sh
```

Install Prometheus and Grafana by Helm, generate the ten dashboards from
`observability/dashboards.py`, and load them as a labelled ConfigMap the Grafana
sidecar picks up. The last lines print the NodePort and admin password.

### Step 23

Same SSH terminal as Step 22. Run this if you missed the password.

```
kubectl -n monitoring get secret grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

Username is always `admin`. Password is the string that just printed — it is different every install. Do not commit it.

### Step 24

New Mac terminal (local), or the Step 7 Mac terminal. Do not SSH.

```
open http://127.0.0.1:31495
```

Log in as `admin` with the password from Step 23. Dashboards are under **Dashboards** — names start with `This lab /`. They stay mostly flat until Locust is running.

---

## Locust

### Step 25

Same Mac terminal as Step 24. Do not SSH.

```
pip install -r requirements-load.txt
```

Locust is a separate requirements file — it is not part of the lab runtime.

### Step 26

Same Mac terminal as Step 25. Locust holds this terminal until you stop it with Ctrl-C.

```
locust -f app/locustfile.py --host http://127.0.0.1:8080
```

`ChatUser` sends text and vision chat completions with a 0.4-1.2 s think time,
through the tunnel to orch-serve. Wait until the terminal says the web UI is on port 8089.

### Step 27

New Mac terminal (local). Do not SSH. Leave Locust running.

```
open http://127.0.0.1:8089
```

### Step 28

Same Locust browser tab. Do not change the host — it must stay `http://127.0.0.1:8080`.

Set **Number of users** to `4` and **Ramp up** (spawn rate) to `2`. Click **Start**. Let it run at least one minute so Grafana `rate()` panels have samples. Then click **Stop**.

A burst of `429` / `tenant_tokens` is admission doing its job — the REPL's
default cap is 10 000 tokens/min for the whole `lab` tenant.

---

## Crew flood + Grafana walk

### Step 29

Same Mac terminal as Step 27 (the one that is not blocked by Locust). Do not SSH.

```
ORCH_URL=http://127.0.0.1:8080/v1 python -m app.crew_flood --rounds 48 --workers 8
```

A threaded multi-agent burst — 8 workers over 48 rounds. Unlike Locust's steady
think-time load, this arrives all at once, which is what makes the queue and
shed panels move.

### Step 30

Mac browser, Grafana already open at http://127.0.0.1:31495. Open the This lab dashboards in this order:

1. This lab / Cluster
2. This lab / Success and failures
3. This lab / Overview (metrics.py)
4. This lab / Gateway + admission
5. This lab / Router
6. This lab / KEDA
7. This lab / HAMi slices
8. This lab / Mooncake KV
9. This lab / Pods and replicas
10. This lab / vLLM

Mooncake hops stay `0` until Step 16 `hop`. Evicts stay `0` until Step 17 `evict`. Overflow / sticky stay `0` unless those paths fire.

### Step 31

Step 2 SSH terminal. Already on the box — do not SSH again.

```
curl -sf http://127.0.0.1:8080/metrics | head
```

The orchestrator's own metrics, straight from the source rather than through Grafana.

### Step 32

Same SSH terminal as Step 31.

```
curl -sf http://127.0.0.1:50051/metrics
```

Mooncake now exposes `/metrics` too (10 adds it) — hop and block counts, the
numbers behind the Mooncake KV dashboard.

### Step 33

Terminate your Lambda instance hahaha!!!

**Do this.** The cluster bills by the hour whether or not you are looking at it.
