# Topology — why `cluster.yaml` says what it says

The config is deliberately terse. This is the reasoning behind it, and the list of
things the renderer refuses.

```
python -m infra.render infra/config/cluster.yaml --plan    # what lands where
python -m infra.render infra/config/cluster.yaml --env     # the gateway's env
python -m infra.render infra/config/cluster.yaml --host gpu-a -o /tmp/a.yaml
```

Part of the [cluster docs](README.md). Overview: [ARCHITECTURE.md](ARCHITECTURE.md)

---

## One file, because two copies cannot be compared

The lab's manifests hardcoded model, TP, `max-model-len` and
`gpu-memory-utilization` **twice** — once per worker. Changing the model meant
editing Kubernetes YAML in two places, and "same cluster, only the policy
changed" became a claim nobody could check.

Everything that varies now appears once, and the manifests are generated. They
carry a `# GENERATED` header for the same reason.

---

## Hosts are independent, and addresses are not topology

Each host is its own single-node k3s cluster. There is no multi-node Kubernetes,
and that is a choice: **the gateway already federates by URL list**
(`PREFILL_URLS` / `DECODE_URLS`), so a second box is a second set of URLs rather
than a second control plane to debug.

The consequence is a real restriction, and the right one: **a tensor-parallel
group cannot span hosts.** TP all-reduces every layer and expects NVLink; over
Ethernet it is unusable rather than merely slow. The renderer refuses it instead
of letting it surface as "the cluster is mysteriously slow".

Addresses and keys live in `.env`, resolved by host name:

| Host | Address | Key |
|---|---|---|
| `gpu-a` | `LAMBDA_HOST_GPU_A`, or `LAMBDA` if it is the only host | `LAMBDA_SSH_KEY_GPU_A`, or `LAMBDA_SSH_KEY` |

An IP is a fact about today, not about the shape of the cluster, so re-renting a
box is an `.env` edit rather than a commit — and a private key path is never
committed at all. `--plan` works with addresses unset; `--env` refuses and names
the variable to set.

`LAMBDA` is accepted **only** when there is exactly one host. With two boxes it is
ambiguous, and silently applying it to both would point every URL at one machine
while the plan claimed two — a one-worker run reported as two.

---

## Aggregated or disaggregated

| Mode | Pools | KV hop |
|---|---|---|
| `aggregated` | `engine` — each server does prefill and decode for its own requests | none |
| `disaggregated` | `prefill` and `decode` — prefill produces KV, decode consumes it | yes |

**Pool names are the gateway's contract, not labels.** `router/pools.py` reads
`PREFILL_URLS` and `DECODE_URLS` and nothing else, so `aggregated` emits one
pool's URLs into both lists — which `pools.py` reads as a single set of engines
doing both phases. Mismatched names are refused.

`kv_transport` is **required** by disaggregated and **refused** by aggregated,
where it would be read nowhere and look configured.

Only `mooncake` moves bytes. `nccl` and `nixl` are four-line no-op stubs, so hops
vanish from Mooncake's `/hops` while requests still succeed: **zero hops does not
prove the hop did not happen.** The renderer says so if you select one.

### `split: capability` is overloaded

Worth knowing before setting it. The gateway reuses `PREFILL_URLS` as the **text**
pool and `DECODE_URLS` as the **vision** pool, with no hop involved
(`router/pools.py:298`).

The pools then run different models and stop being interchangeable, so placement
becomes a capability lookup — a vision request has exactly one destination — and
the admission comparison loses its point, because there is no choice left to
make. Fine for a demo. Not a measurement.

---

## Whole GPUs, never slices

The lab sliced one card with HAMi (`gpumem: 16384`, `gpucores: 55/35`) because it
served a 3B model on a single GPU. That is why class 9 worked on one A100.

At 72B a slice cannot hold the weights, and the failure mode is worse than an
error: **two slices of one card satisfying a request for two GPUs would
masquerade as TP=2 across two cards.** Wrong KV capacity, no NVLink, no second
HBM pool — and every number from the run invalid, with nothing reporting it.

So `model.slicing: true` is refused, manifests request `nvidia.com/gpu` only, and
a test asserts `gpumem` and `gpucores` never appear in rendered output. HAMi's
scheduler may still be installed by `lambda_k3s_hami.sh`; it is simply unused.

---

## Prefix caching is a flag because it is an axis

**On** for agentic traffic. A research agent resends the same system prompt and
tool schemas every turn; without the cache, every turn re-prefills them.

**Off** makes prefill cost real and brings the KV wall forward, so a policy
reading KV pressure fires earlier. That is a legitimate run.

What is **not** legitimate is comparing a policy *across* the setting. The cache
state is what changed, not the policy. The renderer prints this when the flag is
off, because it is the kind of mistake that produces a confident wrong answer.

---

## What the renderer refuses

Each of these fails in milliseconds with the offending number named, rather than
ten minutes into a weights download on a rented GPU.

| Refusal | Why it matters |
|---|---|
| Pools that do not match the mode | The names are the gateway's env contract |
| Disaggregated with no `kv_transport` | Decode engines with no KV to consume |
| Aggregated **with** a transport | Read nowhere; looks configured |
| `model.slicing: true` | A slice masquerading as a second card |
| A pool on an undeclared host | A typo that would otherwise deploy nothing |
| Host GPUs oversubscribed | Accounted **per host** — a global count would approve 4 GPUs spread over two 2-GPU boxes |
| A TP group larger than one host | TP cannot cross the network |
| Weights that do not fit | See below |
| `overflow.on` containing 429 | A tenant over its own quota must not be sent to paid capacity — that is overspend, not capacity |
| An address or key in the config | Refused rather than ignored, because ignoring it means someone commits a key path and believes it is in use |

### The weight fit, and why the obvious version of it is wrong

The check that earns its place. Weights are estimated from the parameter count in
the model's name and divided across `TP × PP`:

```
vllm-prefill-0: Qwen/Qwen2.5-72B-Instruct at bf16 is ~144 GB, so ~72.0 GB per card
at TP x PP = 2. Host 'gpu-a' offers ~72.0 GB usable (80 GB x 0.9), leaving
~0.0 GB for KV. It does not fit with room to serve.
```

The first version refused only when per-card weights **exceeded** usable memory.
That passes 72B bf16 at TP=2, which lands on the exact boundary — ~72.0 GB against
~72.0 GB — and the parameter count comes from a *string*, so a decision taken
there is a coin flip.

It now requires a margin for KV, because **weights fitting is not the bar; weights
fitting with room to serve is.** A cluster that loads the model and then rejects
every request is not a success. Below 25% headroom it warns: admission would bite
on capacity we chose rather than pressure the workload created.

A model whose name carries no parameter count returns `None` and the check skips,
rather than inventing a number to refuse on.

---

## Secrets

`OVERFLOW_API_KEY` is the only secret in the config's subject matter, and it is
not in the config. Everything else about overflow — provider, base URL, model,
caps — is a choice that should be reviewable in a diff, so the renderer **emits**
those as `OVERFLOW_*` variables rather than having them typed in two places where
they would drift.

A test walks the parsed config for any assigned key named like a secret, rather
than grepping for the word — the file *mentions* `OVERFLOW_API_KEY` in order to
say where it is not.
