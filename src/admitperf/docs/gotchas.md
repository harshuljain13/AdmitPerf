# Things that will confuse you

Each of these cost real time. They are collected here because every one looks
like a different problem than it is.

**`READY 1/1` does not mean serving.** Without a readiness probe a pod reports
ready minutes before vLLM binds its port, while the engine downloads weights and
compiles CUDA graphs. It looks exactly like a crash, and any latency measured in
that window is meaningless. The manifests `infra/render.py` generates include a
probe; the originals vendored from module 10 did not.

**The engine's own traceback is not the error.** When the engine core dies, the
API server prints an asyncio traceback ending in "see root cause above". The real
error is on the `(EngineCore_DP0 ...)` lines before it, which `--tail` usually
cuts off. Use `kubectl logs ... | grep -v '(APIServer'`.

**The gateway forwards only `model`, `messages` and `max_tokens` to vLLM.** Tool
definitions are dropped with no error, and an agent then answers in prose as
though it chose not to use its tools. Image parts survive, because they live
inside `messages`.

**`nccl.py` and `nixl.py` are empty stubs.** They return immediately and move
nothing at all. Mooncake is the one that records hops. Zero hops on a dashboard
does not prove a hop did not happen — check `KV_BACKEND` first.

**There is no queue in the gateway.** Requests block on a thread and wait for
vLLM. The real queue is inside the engine, which is why `queue_depth` is scraped
from `/metrics` rather than read locally.

**An image costs twice.** Bytes at the gateway before admission, tokens in KV
after. The first is spent even on requests you refuse, which is an argument for
checking payload size in guardrails.

**Grafana's NodePort is not pinned.** It is allocated randomly, so it usually
will not match the port an SSH tunnel forwards. `kubectl port-forward` sidesteps
it entirely.

**A small model makes the GPU abundant.** On a 7B model an 80 GB card never fills
its KV cache, so every admission policy sits inert and every run produces the
same numbers. This has already happened here once:
`experiments/half-capacity-headroom` recorded `waiting_requests` identically zero
across three repeats.
