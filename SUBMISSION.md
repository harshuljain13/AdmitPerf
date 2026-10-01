# Submission — design the cluster and serve an app

Answers to the project instructions, part by part. Each part is filled in only
once it has been decided and, where it is a number, measured.

Every figure is labelled **predicted** or **measured**. Predictions are written
before the run and checked after. They are not edited to match.

---

## Part 0 — The application

**Both tracks — an agent that retrieves.** A research agent that reads documents,
built on AgentShip. Given a document it extracts the text and figures, answers
questions about it, and searches or fetches more when it needs to.

Documents carry text and images, so the model has to take both. One
vision-language model serves everything, which keeps both workers identical.

| | Tokens |
|---|---|
| **Shared** | System prompt + tool schemas — identical every step, every user |
| **Unique** | The question |
| **Unique** | Extracted document text |
| **Unique** | Figures, once the vision encoder has turned them into tokens |
| **Unique** | Conversation so far, growing each step |

AgentShip does the parsing; the engine never sees a PDF. What reaches the gateway
is an ordinary chat completion whose content happens to include image parts.

One document arrives as one very large prefill — roughly 1k shared tokens, ~15k of
extracted text, ~1-2k per figure. Admission is therefore not deciding about a chat
message. It is deciding whether to let a single request take most of a worker's
cache.

**Token counts: measured at Part 8**, from a real session rather than estimated here.

## Part 1 — Capacity on paper

**4 × H100-80GB**, one node. `Qwen2.5-VL-72B-Instruct` at **fp8**, TP=2, two
workers. `max_model_len` 32,768.

### kv_bytes_per_token

```
2 (K and V) × layers × kv_heads × head_dim × dtype_bytes
2 × 80 × 8 × 128 × 2  =  327,680 bytes  =  320 KiB per token
```

KV stays fp16 even though the weights are fp8 — `kv_cache_dtype` is a separate
knob. Setting it to fp8 would halve this and double concurrency; we are not,
because the cache filling is the thing being studied.

*Layer and head counts to be confirmed from the model's `config.json`.*

### What fits

Per card: 80 GB × 0.90 utilisation = **72 GB usable**, minus **~36.5 GB** of fp8
weights (73 GB across TP=2) = **~35.5 GB free**. Two cards per worker, so roughly
**65 GB of KV** per worker once activations are taken out.

```
max_concurrent_seqs ≈ KV bytes available / (kv_bytes_per_token × max_len)
```

| At | Predicted concurrent, per worker | Across both |
|---|---|---|
| **32,768** — engine max | **~6** | ~12 |
| **8,192** — a short session | ~24 | ~48 |

Six concurrent full-length requests per worker. A single document request at
~20k tokens takes roughly a third of one worker's cache on its own.

### Predicted limiter: **KV**

Weights fit with ~35 GB a card to spare, so not weights. TP=2 runs over NVLink
inside one node, so not interconnect. At six concurrent sequences the batch is
far from compute-bound. `max_num_seqs` is 64, well above what KV allows, so the
scheduler is not binding either.

**Written before the run.** Checked at Part 8: if `gpu_cache_usage_perc`
saturates before `num_requests_waiting` grows, the prediction held.

### If the model changes

`kv_bytes_per_token` moves with layers and KV-head count, not parameter count. A
model with more layers or fewer GQA groups costs more per token at the same size.
Any switch gets restated here with the new figure.

**Why not bf16.** 72B in bf16 is ~145 GB; at TP=2 that is 72.5 GB a card against
~72 GB usable, so the weights alone do not fit. TP=4 would fit them but collapses
the fleet to one worker, removing the placement decision this project is about.

## Part 2 — Design the cluster

*Not answered yet.*

## Part 3 — Guardrails, admit, stay vs leave

*Not answered yet.*

## Part 4 — Place

*Not answered yet.*

## Part 5 — Queue

*Not answered yet. Notebook.*

## Part 6 — Hop and warmth

*Not answered yet.*

## Part 7 — Wire the app to the cluster

*Not answered yet.*

## Part 8 — Proof

*Not answered yet.*

---

## The questions to be answered at Part 8

Kept here from the start so the build aims at them rather than discovering them
at the end.

- What is the app, and which tokens are shared vs unique?
- What dies at guardrails vs admit vs place vs queue?
- Where do I prevent work that will time out?
- Where do I protect KV?
- Where do I prioritise interactive traffic?
- Where do I stop one tenant from owning the GPU?
- Where do I hop, and what is not copied?
- Where do I evict, and what becomes a ghost if I skip it?
- Where does the engine scheduler sit versus my admit / place / queue?
- What limited concurrency on this GPU for this app?
- Four production alerts I would set?
- If I scale, which pool — prefill tokens or decode slots?
- What would I change at 10× traffic, and which three knobs are the wrong next move?
