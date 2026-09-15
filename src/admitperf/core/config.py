"""One definition of every setting, in one place.

Before this, the same default lived in three files — the CLI flag, the
provisioner dataclass, and the Modal app's own `os.environ.get` fallback.
Change one and forget another, and a deploy silently uses a value different
from the one the CLI just printed.

Here the defaults exist exactly once. The CLI overrides them, YAML overrides
them, and the Modal app receives the *resolved* config rather than re-deriving
it. `.env` is for secrets only.

The four sections mirror the four separable problems: getting infrastructure,
getting traffic, choosing a policy, and running the comparison.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """A config that cannot produce a working deployment."""


@dataclass
class EngineConfig:
    """vLLM server settings.

    Everything here changes the numbers, so everything here is recorded in the
    run manifest. Two of these used to be hardcoded and unrecorded, which meant
    a reader could not tell whether a policy difference was real or an artifact
    of prefix caching.
    """

    #: The bottleneck that creates queueing. Deliberately small — with a
    #: generous limit the fleet never saturates and every admission policy
    #: scores identically.
    max_num_seqs: int = 8
    max_model_len: int = 16384
    gpu_memory_utilization: float = 0.90

    tensor_parallel_size: int = 1
    pipeline_parallel_size: int = 1

    #: Off by default for benchmarking. With it on, repeated prompts are served
    #: from cache, prefill cost collapses, and KV pressure stops reflecting
    #: offered load — which is the signal most policies decide on.
    enable_prefix_caching: bool = False

    #: vLLM's own queue discipline, which interacts with admission control.
    #: Recorded explicitly so results say which one produced them.
    scheduling_policy: str = "priority"

    dtype: str = "auto"
    quantization: str | None = None
    max_num_batched_tokens: int | None = None
    swap_space_gb: int | None = None
    block_size: int | None = None

    #: Escape hatch for flags this schema does not model yet.
    extra_args: list[str] = field(default_factory=list)

    def to_vllm_args(self, *, port: int, served_model_name: str) -> list[str]:
        args = [
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--served-model-name",
            served_model_name,
            "--max-num-seqs",
            str(self.max_num_seqs),
            "--max-model-len",
            str(self.max_model_len),
            "--gpu-memory-utilization",
            str(self.gpu_memory_utilization),
            "--tensor-parallel-size",
            str(self.tensor_parallel_size),
            "--pipeline-parallel-size",
            str(self.pipeline_parallel_size),
            "--scheduling-policy",
            self.scheduling_policy,
            "--dtype",
            self.dtype,
        ]
        args.append(
            "--enable-prefix-caching"
            if self.enable_prefix_caching
            else "--no-enable-prefix-caching"
        )
        if self.quantization:
            args += ["--quantization", self.quantization]
        if self.max_num_batched_tokens:
            args += ["--max-num-batched-tokens", str(self.max_num_batched_tokens)]
        if self.swap_space_gb:
            args += ["--swap-space", str(self.swap_space_gb)]
        if self.block_size:
            args += ["--block-size", str(self.block_size)]
        return args + list(self.extra_args)


@dataclass
class InfraConfig:
    """Where the engine runs and what it runs."""

    provider: str = "modal"
    gpu: str = "A10G"
    gpu_count: int = 1
    model: str = "Qwen/Qwen3-0.6B"
    served_model_name: str = "lab"
    #: Modal scales to zero after this; also how long an idle box costs nothing.
    scaledown_window_s: int = 300
    startup_timeout_s: int = 900
    #: Concurrent requests the serving container accepts. Must exceed the
    #: engine's max_num_seqs, or the platform queues in front of the engine and
    #: the benchmark measures the platform instead.
    max_concurrent_inputs: int = 256
    engine: EngineConfig = field(default_factory=EngineConfig)

    @property
    def modal_gpu(self) -> str:
        """Modal's GPU spec: 'A100' for one, 'A100:4' for four."""
        return self.gpu if self.gpu_count <= 1 else f"{self.gpu}:{self.gpu_count}"

    def validate(self) -> None:
        """Catch what would otherwise fail ten minutes into a deploy.

        A tensor-parallel size larger than the GPUs available is the classic
        one: vLLM starts, downloads weights, and only then discovers it cannot
        shard. Cheaper to refuse here.
        """
        if self.gpu_count < 1:
            raise ConfigError(f"gpu_count must be >= 1, got {self.gpu_count}")

        needed = self.engine.tensor_parallel_size * self.engine.pipeline_parallel_size
        if needed > self.gpu_count:
            raise ConfigError(
                f"tensor_parallel_size × pipeline_parallel_size = {needed}, "
                f"but gpu_count is {self.gpu_count}. Raise gpu_count or lower "
                "the parallel sizes — vLLM would fail after the weights download."
            )
        if not 0.0 < self.engine.gpu_memory_utilization <= 1.0:
            raise ConfigError(
                f"gpu_memory_utilization must be in (0, 1], "
                f"got {self.engine.gpu_memory_utilization}"
            )
        if self.engine.scheduling_policy not in {"fcfs", "priority"}:
            raise ConfigError(
                f"scheduling_policy must be fcfs or priority, got {self.engine.scheduling_policy!r}"
            )


@dataclass
class WorkloadConfig:
    kind: str = "poisson"
    n: int = 200
    rate: float = 10.0
    seed: int = 0

    def validate(self) -> None:
        if self.n < 1:
            raise ConfigError(f"workload.n must be >= 1, got {self.n}")
        if self.rate <= 0:
            raise ConfigError(f"workload.rate must be > 0, got {self.rate}")


@dataclass
class PolicySpec:
    """A policy and the arguments it is constructed with."""

    name: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str | dict[str, Any]) -> PolicySpec:
        if isinstance(raw, str):
            return cls(name=raw)
        params = {k: v for k, v in raw.items() if k != "name"}
        return cls(name=raw["name"], params=params)

    @property
    def label(self) -> str:
        """Distinguishes two runs of the same policy at different settings."""
        if not self.params:
            return self.name
        bits = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.name}[{bits}]"


@dataclass
class BenchConfig:
    #: A live engine gives a different number every run. One sample per policy
    #: is not a comparison, so repeats are first-class.
    repeats: int = 1
    #: Requests to discard at the start, while caches and CUDA graphs warm.
    warmup: int = 0
    scrape_interval_s: float = 0.1
    max_state_age_s: float = 5.0
    max_defers: int = 100

    def validate(self) -> None:
        if self.repeats < 1:
            raise ConfigError(f"bench.repeats must be >= 1, got {self.repeats}")
        if self.warmup < 0:
            raise ConfigError(f"bench.warmup must be >= 0, got {self.warmup}")


@dataclass
class Deployment:
    """One provisioned engine, and the label its results are filed under."""

    label: str
    infra: InfraConfig


@dataclass
class ExperimentConfig:
    """Everything one experiment needs, in one object."""

    name: str = "unnamed"
    infra: InfraConfig = field(default_factory=InfraConfig)
    #: Optional sweep: several deployments, every policy run against each.
    #:
    #: The unit of comparison is the deployment, never the matrix. Policies are
    #: comparable to each other only when they faced the same engine on the
    #: same hardware, so results are filed per deployment and `bench compare`
    #: refuses to pool across them. A table mixing an A10G row with an A100 row
    #: would be measuring hardware, not admission control.
    matrix: list[InfraConfig] = field(default_factory=list)
    workload: WorkloadConfig = field(default_factory=WorkloadConfig)
    policies: list[PolicySpec] = field(default_factory=lambda: [PolicySpec("no_admission")])
    bench: BenchConfig = field(default_factory=BenchConfig)

    def deployments(self) -> list[Deployment]:
        """Every engine this experiment needs, in order.

        Without a matrix that is one deployment, which is the common case and
        the only one where a single comparison table is meaningful.
        """
        if not self.matrix:
            return [Deployment(label=self._label(self.infra), infra=self.infra)]
        return [Deployment(label=self._label(i), infra=i) for i in self.matrix]

    @staticmethod
    def _label(infra: InfraConfig) -> str:
        """A short, filesystem-safe description of what makes this deployment
        different from its siblings."""
        model = infra.model.rsplit("/", 1)[-1]
        e = infra.engine
        bits = [model, infra.modal_gpu.replace(":", "x"), f"seqs{e.max_num_seqs}"]
        if e.tensor_parallel_size > 1:
            bits.append(f"tp{e.tensor_parallel_size}")
        if e.enable_prefix_caching:
            bits.append("prefixcache")
        return "-".join(b.replace("/", "-") for b in bits)

    def validate(self) -> ExperimentConfig:
        self.infra.validate()
        for entry in self.matrix:
            entry.validate()
        labels = [d.label for d in self.deployments()]
        if len(labels) != len(set(labels)):
            raise ConfigError(
                f"matrix entries produce duplicate labels {labels}; they differ "
                "only in settings the label does not capture, so their results "
                "would overwrite each other"
            )
        self.workload.validate()
        self.bench.validate()
        if not self.policies:
            raise ConfigError("at least one policy is required")
        seen = [p.label for p in self.policies]
        if len(seen) != len(set(seen)):
            raise ConfigError(f"duplicate policy entries: {seen}")
        return self

    # --- loading ----------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExperimentConfig:
        infra_raw = dict(raw.get("infra") or {})
        engine_raw = infra_raw.pop("engine", None) or {}

        def _infra(entry: dict[str, Any]) -> InfraConfig:
            """A matrix entry inherits the base infra and overrides parts of it,
            so a sweep states only what varies."""
            merged = {**infra_raw, **{k: v for k, v in entry.items() if k != "engine"}}
            engine = {**engine_raw, **(entry.get("engine") or {})}
            return InfraConfig(**merged, engine=EngineConfig(**engine))

        matrix_raw = raw.get("matrix") or []
        # .get with a default, not `or`: an explicitly empty list means the
        # caller asked for no policies, which validate() rejects. `or` would
        # silently substitute the default and run something unrequested.
        policies_raw = raw.get("policies", ["no_admission"])

        cfg = cls(
            name=raw.get("name", "unnamed"),
            infra=InfraConfig(**infra_raw, engine=EngineConfig(**engine_raw)),
            matrix=[_infra(e or {}) for e in matrix_raw],
            workload=WorkloadConfig(**(raw.get("workload") or {})),
            policies=[PolicySpec.parse(p) for p in policies_raw],
            bench=BenchConfig(**(raw.get("bench") or {})),
        )
        return cfg.validate()

    @classmethod
    def load(cls, path: str | Path) -> ExperimentConfig:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"no config at {p}")
        try:
            raw = yaml.safe_load(p.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{p} must contain a mapping at the top level")
        try:
            return cls.from_dict(raw)
        except TypeError as exc:
            # Raised when YAML carries a key no dataclass declares. Naming the
            # file and the message beats a bare TypeError from dataclass init.
            raise ConfigError(f"{p}: {exc}") from exc

    def with_overrides(self, **kw: Any) -> ExperimentConfig:
        """Apply non-None CLI overrides. Flags beat the file."""
        infra, workload, bench = self.infra, self.workload, self.bench
        engine = infra.engine

        for key in ("gpu", "gpu_count", "model", "provider"):
            if kw.get(key) is not None:
                infra = replace(infra, **{key: kw[key]})
        for key in (
            "max_num_seqs",
            "max_model_len",
            "tensor_parallel_size",
            "pipeline_parallel_size",
            "enable_prefix_caching",
            "scheduling_policy",
        ):
            if kw.get(key) is not None:
                engine = replace(engine, **{key: kw[key]})
        for key in ("n", "rate", "seed"):
            if kw.get(key) is not None:
                workload = replace(workload, **{key: kw[key]})
        for key in ("repeats", "warmup"):
            if kw.get(key) is not None:
                bench = replace(bench, **{key: kw[key]})

        policies = self.policies
        if kw.get("policy"):
            policies = [PolicySpec.parse(p) for p in kw["policy"]]

        return replace(
            self,
            name=kw.get("name") or self.name,
            infra=replace(infra, engine=engine),
            workload=workload,
            policies=policies,
            bench=bench,
        ).validate()

    def to_dict(self) -> dict[str, Any]:
        """The resolved config, for the run manifest.

        What actually ran, rather than what was asked for — the two differ
        whenever a flag overrode a file.
        """
        return asdict(self)

    def engine_env(self) -> str:
        """Resolved engine settings, as JSON for the Modal app to read back.

        Passing the whole thing rather than a scatter of variables is what
        keeps the defaults defined in exactly one place.
        """
        return json.dumps(
            {
                "model": self.infra.model,
                "served_model_name": self.infra.served_model_name,
                "gpu": self.infra.modal_gpu,
                "scaledown_window_s": self.infra.scaledown_window_s,
                "startup_timeout_s": self.infra.startup_timeout_s,
                "max_concurrent_inputs": self.infra.max_concurrent_inputs,
                "engine": asdict(self.infra.engine),
            }
        )


__all__ = [
    "BenchConfig",
    "Deployment",
    "ConfigError",
    "EngineConfig",
    "ExperimentConfig",
    "InfraConfig",
    "PolicySpec",
    "WorkloadConfig",
]
