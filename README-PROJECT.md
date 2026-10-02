# AdmitPerf — admission control on a cluster we own

*Project README. The library README is [`README.md`](README.md).*

When more requests arrive than a GPU can serve, something has to turn some away.
There are several ways to decide, each defensible, each wrong somewhere — and no
way to find out which suits your system, because published results share no
baseline and almost never report whether the policy ever fired at all.

This repo is the thing that finds out: a real cluster, a real application, and
admission policies you swap by configuration while everything else holds still.

---

## Layout

| Directory | What it is |
|---|---|
| `infra/` | The cluster. Manifests, bring-up scripts, and the renderer that generates them from one config |
| `client_app/` | What talks to the cluster. Open WebUI now, AgentShip later |
| `src/admitperf/` | The library: core, policies, bench, experiments, reports, docs |
| `SUBMISSION.md` | The cohort write-up, answered part by part |

---

## The cluster

One config describes it. Manifests are generated, never hand-edited — otherwise
"same cluster, only the policy changed" is a claim nobody can check.

```bash
python -m infra.render infra/config/cluster.yaml -o workers.yaml
python -m infra.render infra/config/cluster.yaml --urls
```

**`Qwen2.5-VL-72B-Instruct`, fp8, TP=2, two workers on 4×H100-80GB.**

Three decisions behind that, each with a reason:

- **72B, not 7B.** The brief this project answers opens with *"the GPU is scarce."*
  A small model on an 80 GB card makes it abundant: KV never fills, the queue
  never backs up, and every policy sits inert. That failure has already happened
  here once — `experiments/half-capacity-headroom` recorded `waiting_requests`
  identically zero across three repeats, so every signal was flat and the run
  isolated nothing.
- **VL, not text-only.** The app reads documents containing text *and* images. A
  vision-language model handles both in one prompt, so **both workers run the
  same model and stay interchangeable** — placement remains a load decision. A
  separate vision worker would give a vision request exactly one destination,
  and a placement decision with one option is not a decision.
- **fp8, TP=2.** In bf16, 72B is ~145 GB — at TP=2 that is 72.5 GB a card against
  ~72 GB usable, so the weights alone do not fit. fp8 halves it. TP=4 would fit
  bf16 but collapses the fleet to a single worker, removing the thing being
  studied.

---

## The application

A research agent that reads documents. It is given a document, extracts the text
and figures, and answers questions about it — searching and fetching more when it
needs to.

**AgentShip parses; the engine never sees a PDF.** Document parsing, figure
extraction and chunking are library work in the app. What reaches the gateway is
an ordinary chat completion whose content happens to include image parts.

**Shared:** system prompt and tool schemas, identical on every step and every
user — the part prefix caching can reuse.
**Unique:** the question, the document text, the figures, the conversation so far.

One document becomes one very large prefill — roughly 1k shared tokens, ~15k of
extracted text, and ~1–2k per figure. That matters: admission is not deciding
about a chat message, it is deciding whether to let a single request take most of
a worker's cache. Chat traffic is small and uniform and never asks that question.

---

## What gets measured

Every admission decision records the verdict **and the signal value behind it**.
That second half is the point. A policy that refuses above 90% KV pressure,
evaluated on a system that never passed 40%, never refused anything — and reports
the same numbers as no policy at all. Without the signal range, those two are
indistinguishable, which is the gap a review of 55 papers found and nobody fills.

Two runs make the argument:

- **Same policy, two load levels.** At low load the signal never crosses its
  threshold and the policy is inert. At high load it fires. Same code, opposite
  conclusions, and a headline number alone cannot tell you which you have.
- **Same policies, two traffic mixes.** Shared-prefix-heavy against unique-heavy.
  If the ranking flips, a result from one setup does not transfer to another.

---

## Status

- [x] Cluster vendored, manifests generated from config
- [x] Repo restructured: src/admitperf · infra · client_app
- [ ] Admission reads a policy from the registry instead of a hardcoded check
- [ ] Signal value recorded with every decision
- [ ] Agent wired to the gateway
- [ ] Runs, and the report

---

## Things that will confuse you

Each of these cost real time already.

- **`READY 1/1` does not mean serving.** Without a readiness probe a pod reports
  ready minutes before vLLM binds its port, while the engine compiles. It looks
  exactly like a crash. The generated manifests add a probe; the originals had none.
- **The gateway forwards only `model`, `messages` and `max_tokens`.** Tool
  definitions are dropped silently and the agent answers in prose as though it
  chose not to use its tools. Image parts survive, because they live inside
  `messages`.
- **`nccl.py` and `nixl.py` are empty stubs.** They return immediately and move
  nothing. Mooncake records the hops.
- **There is no queue in the gateway.** Requests block on a thread; the real
  queue is inside the engine.
- **An image costs twice** — bytes at the gateway before admission, tokens in KV
  after. The first is spent even on requests you refuse.
