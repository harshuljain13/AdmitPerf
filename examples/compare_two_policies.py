"""A working example: what does admission control buy you?

Drives identical traffic through a simulated engine three times — admitting
everything, then with a KV-pressure policy, then with a queue-depth policy — and
compares the reports.

    python examples/compare_two_policies.py                 # one run per arm
    python examples/compare_two_policies.py --repeats 5     # with an error bar

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

import argparse
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
ARRIVALS_PER_TICK = 10  # the MEAN; arrivals are bursty, see below
SLO_TTFT_MS = 1_500

#: Arrivals are Poisson and service times are jittered, so repeats of the same
#: configuration differ. Without that, every repeat is byte-identical, the spread is
#: zero, and the error bar the comparison prints would be theatre — it would report
#: two arms as cleanly separated on the strength of one deterministic run each.
SERVICE_JITTER = 0.35


def _poisson(mean: float) -> int:
    """Knuth's method. Here rather than numpy because this package has no runtime
    dependencies and an example that needs one is not an example."""
    import math

    limit, k, product = math.exp(-mean), 0, random.random()
    while product > limit:
        k += 1
        product *= random.random()
    return k


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
            self.slots.append(self._service())
            self.started[rid] = self.now

    def _service(self) -> int:
        """How long this request holds a slot. Real generations vary in length, and a
        fixed cost makes the queue drain like a metronome."""
        low = int(SERVICE_TICKS * (1 - SERVICE_JITTER))
        high = int(SERVICE_TICKS * (1 + SERVICE_JITTER))
        return random.randint(low, high)

    def accept(self, request_id: str) -> None:
        """Take an admitted request. It waits if every slot is busy."""
        self.arrived[request_id] = self.now
        if len(self.slots) < MAX_CONCURRENT:
            self.slots.append(self._service())
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


def run(policy, seed: int = 0) -> None:
    """Drive traffic through one policy.

    The seed is shared between arms within a repeat, so the two arms see the same
    traffic — otherwise a difference between them could be a difference between
    traffic patterns and the comparison would be measuring the generator. It CHANGES
    between repeats, because repeats of an identical trace have no spread to report
    and would make the error bar look like zero.
    """
    random.seed(seed)
    engine = FakeEngine()
    admitted: list[str] = []
    sent = 0

    with policy:
        while sent < REQUESTS:
            engine.tick()
            # Poisson arrivals, not a fixed rate. Bursts are the whole reason
            # admission control exists; a smooth stream never tests it.
            for _ in range(_poisson(ARRIVALS_PER_TICK)):
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


def main(repeats: int = 1) -> None:
    OUT.mkdir(exist_ok=True)
    arms = {
        "no admission (baseline)": (OUT / "baseline", lambda log: NoAdmission(log=log)),
        "kv_threshold 0.90": (
            OUT / "kv_threshold",
            lambda log: KvThreshold(threshold=0.90, log=log),
        ),
        f"queue_depth {MAX_WAITING}": (
            OUT / "queue_depth",
            lambda log: QueueDepth(max_waiting=MAX_WAITING, log=log),
        ),
    }

    for repeat in range(1, repeats + 1):
        for _, (arm_dir, build) in arms.items():
            arm_dir.mkdir(parents=True, exist_ok=True)
            log = arm_dir / f"r{repeat}.jsonl"
            log.unlink(missing_ok=True)
            run(build(log), seed=repeat)

    baseline = arms["no admission (baseline)"][0]
    for name, (arm_dir, _) in arms.items():
        print(f"\n{'=' * 68}\n  {name}  (repeat 1 of {repeats})\n{'=' * 68}")
        print(Report.from_log(arm_dir / "r1.jsonl").text())

    for name, (arm_dir, _) in arms.items():
        if arm_dir == baseline:
            continue
        print(f"\n\n### baseline  vs  {name}")
        print(Comparison.from_logs(baseline, arm_dir).text())

    print(f"\nlogs in {OUT}/  —  a directory per arm, one file per repeat. Try:")
    for _, (arm_dir, _) in arms.items():
        if arm_dir != baseline:
            print(f"  admitperf compare {baseline} {arm_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="runs per arm. One has no error bar, and a gap smaller than the spread "
        "between repeats is not a result.",
    )
    main(ap.parse_args().repeats)
