"""A working example: what does admission control buy you?

Drives identical traffic through a simulated engine three times — admitting
everything, then with a KV-pressure policy, then with a queue-depth policy — and
compares the reports.

    python examples/compare_two_policies.py

The result is the reason the comparison exists. The KV policy cuts p95 TTFT by more
than 10x and makes goodput WORSE, because a threshold chosen without reference to the
SLO refuses requests the cluster could still have served in time. A queue-depth bound
derived from the SLO beats both. A report showing only latency would have called the
first one a win.

No GPU, no cluster, no network. The engine here is forty lines of arithmetic, which
is enough because a policy is a pure function of metrics: it cannot tell a simulated
scrape from a real one. Against a real fleet you change exactly one thing — where the
metrics come from — and the rest of this file is what your gateway already does.

The simulation is deliberately honest about the thing that matters. KV pressure is
driven by *resident tokens*, so it only climbs when requests overlap, and TTFT grows
with queue depth. That is why admission control can help at all, and also why a run
with short requests comes back inert: there is nothing to shed.
"""

from __future__ import annotations

import random
from pathlib import Path

from admitperf.comparison import Comparison
from admitperf.policies import KvThreshold, NoAdmission, QueueDepth
from admitperf.report import Report

OUT = Path("example-runs")

# --- the engine being modelled ------------------------------------------------
# Numbers from a real deployment: one A100-40GB serving Qwen2.5-7B in bf16 leaves
# ~21 GiB of KV at 56 KiB/token. See the README for the arithmetic.
KV_CAPACITY_TOKENS = 384_000
MAX_CONCURRENT = 48  # vLLM's max_num_seqs: only this many are served at once
TOKENS_PER_REQUEST = 8_704  # 8192 prompt + 512 generated
SERVICE_TICKS = 12  # ticks a request occupies a slot
TICK_MS = 50.0

# --- the traffic --------------------------------------------------------------
# Capacity is MAX_CONCURRENT / SERVICE_TICKS = 4 requests per tick. Offering 10 is a
# 2.5x overload, which is the only regime where admission control can do anything:
# below capacity there is nothing to shed and every policy looks identical.
REQUESTS = 400
ARRIVALS_PER_TICK = 10
SLO_TTFT_MS = 1_500


class FakeEngine:
    """Just enough serving behaviour to make admission control mean something.

    A bounded number of slots and a FIFO queue for the rest. That bound is the whole
    model: an engine that serves everything instantly cannot be overloaded, so there
    would be nothing for a policy to protect.
    """

    def __init__(self) -> None:
        self.slots: list[int] = []  # ticks remaining, per request being served
        self.queue: list[str] = []  # admitted, not yet started
        self.now = 0
        self.started: dict[str, int] = {}  # request id -> tick it got a slot
        self.arrived: dict[str, int] = {}

    def tick(self) -> None:
        """Retire finished requests, then start whatever fits."""
        self.now += 1
        self.slots = [t - 1 for t in self.slots if t > 1]
        while self.queue and len(self.slots) < MAX_CONCURRENT:
            rid = self.queue.pop(0)
            self.slots.append(SERVICE_TICKS)
            self.started[rid] = self.now

    def accept(self, request_id: str) -> None:
        """Take an admitted request. It waits if every slot is busy."""
        self.arrived[request_id] = self.now
        if len(self.slots) < MAX_CONCURRENT:
            self.slots.append(SERVICE_TICKS)
            self.started[request_id] = self.now
        else:
            self.queue.append(request_id)

    def ttft_ms(self, request_id: str) -> float | None:
        """Queueing delay plus a fixed prefill. None while still queued — and a
        request that never started is not a fast request, so it must not be
        silently recorded as one."""
        if request_id not in self.started:
            return None
        waited = self.started[request_id] - self.arrived[request_id]
        return 180.0 + waited * TICK_MS

    def metrics(self) -> dict[str, float]:
        """What the host would scrape, in vLLM's own metric names.

        Raw names on purpose — that is what a `Signal` is for. AdmitPerf reads
        `vllm:kv_cache_usage_perc` and hands the policy `kv_pressure`, so the policy
        never learns which engine it is in front of.

        KV pressure tracks resident tokens, not arrivals, which is why it only climbs
        when requests overlap: the reason a run of short requests comes back inert.
        """
        resident = len(self.slots) * TOKENS_PER_REQUEST
        return {
            "vllm:kv_cache_usage_perc": min(1.0, resident / KV_CAPACITY_TOKENS),
            "vllm:num_requests_waiting": float(len(self.queue)),
            "vllm:num_requests_running": float(len(self.slots)),
        }


def run(policy, log: Path) -> None:
    """Drive identical traffic through one policy.

    Identical because the seed is fixed. Without that, a difference between the two
    runs could be a difference between traffic patterns, and the comparison would be
    measuring the generator.
    """
    random.seed(0)
    engine = FakeEngine()
    admitted: list[str] = []
    sent = 0

    with policy:
        while sent < REQUESTS:
            engine.tick()
            for _ in range(ARRIVALS_PER_TICK):
                if sent >= REQUESTS:
                    break
                rid = f"r{sent}"
                sent += 1

                # This is the integration, in full. Everything else is your gateway.
                decision = policy(engine.metrics(), request_id=rid)
                if not decision.admitted:
                    continue  # your gateway would return decision.status here
                engine.accept(rid)
                admitted.append(rid)

        # Drain, then report outcomes. Latency is only known once a request has
        # actually started, so reporting it at admission time would record the
        # queueing delay as zero for everything.
        while engine.queue or engine.slots:
            engine.tick()
        for rid in admitted:
            ttft = engine.ttft_ms(rid)
            if ttft is None:
                policy.outcome(rid, ok=False, status=499)  # never served
            else:
                policy.outcome(rid, ttft_ms=ttft, ok=ttft <= SLO_TTFT_MS, status=200)


#: A queue bound derived from the SLO rather than guessed. The queue drains at
#: MAX_CONCURRENT / SERVICE_TICKS = 4 per tick, so a queue of N costs N/4 ticks of
#: waiting. Staying inside the SLO means N <= (SLO - prefill) / TICK_MS * 4.
MAX_WAITING = int((SLO_TTFT_MS - 180) / TICK_MS * (MAX_CONCURRENT / SERVICE_TICKS))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    arms = {
        "no admission (baseline)": (OUT / "baseline.jsonl", lambda log: NoAdmission(log=log)),
        "kv_threshold 0.90": (
            OUT / "kv_threshold.jsonl",
            lambda log: KvThreshold(threshold=0.90, log=log),
        ),
        f"queue_depth {MAX_WAITING}": (
            OUT / "queue_depth.jsonl",
            lambda log: QueueDepth(max_waiting=MAX_WAITING, log=log),
        ),
    }

    for _, (path, build) in arms.items():
        path.unlink(missing_ok=True)
        run(build(path), path)

    for name, (path, _) in arms.items():
        print(f"\n{'=' * 68}\n  {name}\n{'=' * 68}")
        print(Report.from_log(path).text())

    baseline = OUT / "baseline.jsonl"
    for name, (path, _) in arms.items():
        if path == baseline:
            continue
        print(f"\n\n### baseline  vs  {name}")
        print(Comparison.from_logs(baseline, path).text())

    print(f"\nlogs in {OUT}/  —  try:")
    for _, (path, _) in arms.items():
        if path != baseline:
            print(f"  admitperf compare {baseline} {path}")


if __name__ == "__main__":
    main()
