"""W1 — synthetic request-mix workload with Poisson arrivals.

The point of a synthetic workload is not realism; it is *control*. Admission
control only becomes interesting when offered load exceeds what the fleet can
serve at SLO, and a generator lets us dial straight to that regime instead of
hunting for it in a captured trace.

Poisson arrivals are the standard model for independent request arrivals: gaps
between requests are exponentially distributed, which produces natural bursts
rather than the evenly-spaced stream a fixed interval would give. Bursts are
the whole point — an admission policy that only sees smooth traffic is never
actually tested.

Three SLO classes, because a single deadline makes every policy look alike.
The interesting question is which request a policy sacrifices when it cannot
serve them all.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass

from admitperf.core.api import Request
from admitperf.traces.base import TraceEvent, TraceLoader


@dataclass(frozen=True)
class SLOClass:
    """One traffic class: how often it arrives, how big it is, what it promises.

    Deadlines are read one of two ways. In absolute mode they are
    milliseconds. In relative mode they are *multiples of the engine's unloaded
    latency*, measured by `admitperf infra calibrate` — so "3x" means the same
    thing on a small model and a large one, and a comparison across hardware
    stays meaningful.
    """

    name: str
    weight: float
    input_tokens: tuple[int, int]
    output_tokens: tuple[int, int]
    deadline_ttft_ms: float | None
    deadline_tbt_ms: float | None
    priority: int = 0
    #: Multipliers used when slo_mode is "relative".
    ttft_scale: float = 5.0
    tbt_scale: float = 2.0


@dataclass(frozen=True)
class Baseline:
    """The engine's latency with nothing else running.

    Produced by `admitperf infra calibrate`, which sends a trickle of traffic
    so nothing queues. Relative deadlines are multiples of these.
    """

    ttft_p50_ms: float
    itl_p50_ms: float
    samples: int = 0


#: Defaults roughly mirroring PROPOSAL.md §2: an interactive class with a tight
#: first-token deadline, a streaming class that cares about smoothness, and a
#: batch class that mostly wants to finish.
DEFAULT_CLASSES: tuple[SLOClass, ...] = (
    SLOClass(
        name="interactive",
        weight=0.5,
        input_tokens=(64, 512),
        output_tokens=(32, 256),
        deadline_ttft_ms=500.0,
        deadline_tbt_ms=50.0,
        priority=10,
        ttft_scale=3.0,
        tbt_scale=1.5,
    ),
    SLOClass(
        name="streaming",
        weight=0.3,
        input_tokens=(256, 2048),
        output_tokens=(256, 1024),
        deadline_ttft_ms=2000.0,
        deadline_tbt_ms=100.0,
        priority=5,
        ttft_scale=5.0,
        tbt_scale=2.0,
    ),
    SLOClass(
        name="batch",
        weight=0.2,
        input_tokens=(512, 4096),
        output_tokens=(512, 2048),
        deadline_ttft_ms=30000.0,
        deadline_tbt_ms=None,
        priority=0,
        ttft_scale=10.0,
        tbt_scale=3.0,
    ),
)


class PoissonWorkload(TraceLoader):
    """Generates requests with exponential inter-arrival gaps.

    Fully determined by `seed`: the same seed yields byte-identical events, so
    two runs differ only by the policy under test. That is the property that
    makes a comparison a comparison.
    """

    name = "poisson"

    def __init__(
        self,
        *,
        n_requests: int = 1000,
        rate_per_s: float = 10.0,
        seed: int = 0,
        classes: tuple[SLOClass, ...] = DEFAULT_CLASSES,
        tenants: tuple[str, ...] = ("tenant-a", "tenant-b", "tenant-c"),
        duration_s: float | None = None,
        warmup_s: float = 0.0,
        baseline: Baseline | None = None,
    ) -> None:
        if n_requests < 1:
            raise ValueError(f"n_requests must be >= 1, got {n_requests}")
        if rate_per_s <= 0:
            raise ValueError(f"rate_per_s must be > 0, got {rate_per_s}")
        if not classes:
            raise ValueError("need at least one SLO class")
        if not tenants:
            raise ValueError("need at least one tenant")

        self.n_requests = n_requests
        self.rate_per_s = rate_per_s
        self.seed = seed
        self.classes = classes
        self.tenants = tenants
        self.duration_s = duration_s
        self.warmup_s = warmup_s
        #: When present, deadlines become multiples of the engine's unloaded
        #: latency rather than fixed milliseconds.
        self.baseline = baseline

    def requests(self) -> Iterator[Request]:
        """Yield requests in arrival order.

        A private Random instance, never the module-level one: a policy or an
        engine that happens to call random.random() must not be able to shift
        the workload and make runs incomparable.
        """
        rng = random.Random(self.seed)
        weights = [c.weight for c in self.classes]
        now = 0.0
        i = 0

        while True:
            # Exponential gap — the inverse-CDF of a Poisson process.
            now += rng.expovariate(self.rate_per_s)
            if self.duration_s is not None:
                if now > self.duration_s:
                    return
            elif i >= self.n_requests:
                return

            cls = rng.choices(self.classes, weights=weights, k=1)[0]
            tenant = rng.choice(self.tenants)
            ttft, tbt = self._deadlines(cls)

            yield Request(
                request_id=f"req-{i:06d}",
                tenant_id=tenant,
                arrival_time=now,
                input_tokens=rng.randint(*cls.input_tokens),
                agent_id=None,
                expected_output_tokens=rng.randint(*cls.output_tokens),
                deadline_ttft_ms=ttft,
                deadline_tbt_ms=tbt,
                priority=cls.priority,
                slo_class=cls.name,
                # Warmup requests are sent — they are part of the load the
                # engine faces — but excluded from the results, because they
                # pay for cold caches and graph capture rather than for
                # anything the policy did.
                metadata={"warmup": now < self.warmup_s},
            )
            i += 1

    def _deadlines(self, cls: SLOClass) -> tuple[float | None, float | None]:
        """Absolute milliseconds, or multiples of the engine's unloaded latency.

        Fixed milliseconds do not transfer across hardware: 500ms is generous
        for a 0.5B model and impossible for a 70B one, so a single number would
        be measuring the model rather than the policy.
        """
        if self.baseline is None:
            return cls.deadline_ttft_ms, cls.deadline_tbt_ms
        ttft = cls.ttft_scale * self.baseline.ttft_p50_ms
        tbt = cls.tbt_scale * self.baseline.itl_p50_ms if cls.deadline_tbt_ms else None
        return ttft, tbt

    def events(self) -> Iterator[TraceEvent]:
        """The frozen TraceLoader view of the same stream.

        Single-turn traffic, so each request is its own session of one and
        agent_id mirrors request_id, as traces/base.py specifies.
        """
        for req in self.requests():
            yield TraceEvent(
                time=req.arrival_time,
                request_id=req.request_id,
                agent_id=req.request_id,
                tenant_id=req.tenant_id,
                input_tokens=req.input_tokens,
                expected_output_tokens=req.expected_output_tokens,
                turn_index=0,
                is_final_turn=True,
            )

    def total_events(self) -> int:
        """Exact for a fixed count; an estimate when running for a duration."""
        if self.duration_s is None:
            return self.n_requests
        return int(self.duration_s * self.rate_per_s)

    def total(self) -> int:
        return self.total_events()


__all__ = ["DEFAULT_CLASSES", "Baseline", "PoissonWorkload", "SLOClass"]
