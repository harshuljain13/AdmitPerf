"""The single source of truth for settings.

The value of centralising this is not tidiness — it is that a default cannot
disagree with itself. These tests pin the two things that actually cost money
when wrong: what argv reaches vLLM, and what the validator refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from admitperf.core.config import ConfigError, EngineConfig, ExperimentConfig, PolicySpec


def _args(engine: EngineConfig) -> list[str]:
    return engine.to_vllm_args(port=8000, served_model_name="lab")


def test_parallelism_reaches_vllm() -> None:
    args = _args(EngineConfig(tensor_parallel_size=4, pipeline_parallel_size=2))
    assert args[args.index("--tensor-parallel-size") + 1] == "4"
    assert args[args.index("--pipeline-parallel-size") + 1] == "2"


def test_prefix_caching_is_explicit_in_both_directions() -> None:
    """Never left to vLLM's default: it changes KV pressure enormously, and a
    result that does not state which way it ran cannot be interpreted."""
    assert "--no-enable-prefix-caching" in _args(EngineConfig(enable_prefix_caching=False))
    assert "--enable-prefix-caching" in _args(EngineConfig(enable_prefix_caching=True))


def test_optional_flags_are_omitted_when_unset() -> None:
    args = _args(EngineConfig())
    assert "--quantization" not in args
    assert "--swap-space" not in args


def test_extra_args_pass_through() -> None:
    assert _args(EngineConfig(extra_args=["--foo", "bar"]))[-2:] == ["--foo", "bar"]


def test_modal_gpu_spec_encodes_count() -> None:
    cfg = ExperimentConfig.from_dict(
        {"infra": {"gpu": "A100", "gpu_count": 4, "engine": {"tensor_parallel_size": 4}}}
    )
    assert cfg.infra.modal_gpu == "A100:4"
    assert ExperimentConfig().infra.modal_gpu == "A10G"


def test_parallelism_beyond_gpu_count_is_refused() -> None:
    """The expensive mistake: vLLM would deploy, download weights for minutes,
    and only then discover it cannot shard."""
    with pytest.raises(ConfigError, match="gpu_count"):
        ExperimentConfig.from_dict(
            {"infra": {"gpu_count": 2, "engine": {"tensor_parallel_size": 4}}}
        )


@pytest.mark.parametrize(
    "raw,match",
    [
        ({"infra": {"engine": {"gpu_memory_utilization": 1.5}}}, "gpu_memory_utilization"),
        ({"infra": {"engine": {"scheduling_policy": "lifo"}}}, "scheduling_policy"),
        ({"workload": {"n": 0}}, "workload.n"),
        ({"workload": {"rate": 0}}, "workload.rate"),
        ({"bench": {"repeats": 0}}, "bench.repeats"),
        ({"policies": []}, "at least one policy"),
    ],
)
def test_nonsense_is_refused(raw: dict, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        ExperimentConfig.from_dict(raw)


def test_policy_params_parse_and_label() -> None:
    spec = PolicySpec.parse({"name": "kv_threshold", "threshold": 0.9})
    assert spec.name == "kv_threshold"
    assert spec.params == {"threshold": 0.9}
    assert spec.label == "kv_threshold[threshold=0.9]"
    assert PolicySpec.parse("no_admission").label == "no_admission"


def test_same_policy_at_two_settings_is_allowed() -> None:
    cfg = ExperimentConfig.from_dict(
        {
            "policies": [
                {"name": "kv_threshold", "threshold": 0.8},
                {"name": "kv_threshold", "threshold": 0.9},
            ]
        }
    )
    assert [p.label for p in cfg.policies] == [
        "kv_threshold[threshold=0.8]",
        "kv_threshold[threshold=0.9]",
    ]


def test_exact_duplicates_are_refused() -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        ExperimentConfig.from_dict({"policies": ["no_admission", "no_admission"]})


def test_yaml_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    p.write_text(
        "name: x\n"
        "infra:\n  gpu: H100\n  gpu_count: 2\n  engine:\n    tensor_parallel_size: 2\n"
        "workload:\n  n: 50\n  rate: 5\n"
        "policies:\n  - no_admission\n"
        "bench:\n  repeats: 2\n"
    )
    cfg = ExperimentConfig.load(p)
    assert cfg.name == "x"
    assert cfg.infra.modal_gpu == "H100:2"
    assert cfg.bench.repeats == 2


def test_unknown_key_names_the_file(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("workload:\n  nope: 1\n")
    with pytest.raises(ConfigError, match="bad.yaml"):
        ExperimentConfig.load(p)


def test_missing_file_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="no config at"):
        ExperimentConfig.load("does/not/exist.yaml")


def test_flags_override_the_file() -> None:
    cfg = ExperimentConfig().with_overrides(gpu="H100", n=999, repeats=5, rate=None)
    assert cfg.infra.gpu == "H100"
    assert cfg.workload.n == 999
    assert cfg.bench.repeats == 5
    assert cfg.workload.rate == 10.0  # None override leaves the default alone


def test_engine_env_carries_the_resolved_config() -> None:
    """What the Modal app reads back. If this drifts, the deploy silently uses
    different settings from the ones the CLI printed."""
    cfg = ExperimentConfig.from_dict(
        {
            "infra": {
                "gpu": "A100",
                "gpu_count": 4,
                "engine": {"tensor_parallel_size": 4, "max_num_seqs": 32},
            }
        }
    )
    blob = json.loads(cfg.engine_env())
    assert blob["gpu"] == "A100:4"
    assert blob["engine"]["tensor_parallel_size"] == 4
    assert blob["engine"]["max_num_seqs"] == 32


# --- matrix ---------------------------------------------------------------


def test_no_matrix_means_exactly_one_deployment() -> None:
    """The common case, and the only one where a single table is meaningful."""
    assert len(ExperimentConfig().deployments()) == 1


def test_matrix_entries_inherit_the_base_infra() -> None:
    """A sweep should state only what varies, not repeat the whole config."""
    cfg = ExperimentConfig.from_dict(
        {
            "infra": {
                "model": "m/x",
                "served_model_name": "lab",
                "engine": {"max_model_len": 4096},
            },
            "matrix": [{"gpu": "A10G"}, {"gpu": "A100"}],
        }
    )
    for dep in cfg.deployments():
        assert dep.infra.model == "m/x"
        assert dep.infra.engine.max_model_len == 4096  # inherited
    assert [d.infra.gpu for d in cfg.deployments()] == ["A10G", "A100"]


def test_matrix_entries_override_the_base_engine_settings() -> None:
    cfg = ExperimentConfig.from_dict(
        {
            "infra": {"engine": {"max_num_seqs": 4, "max_model_len": 2048}},
            "matrix": [{"engine": {"max_num_seqs": 32}}],
        }
    )
    engine = cfg.deployments()[0].infra.engine
    assert engine.max_num_seqs == 32  # overridden
    assert engine.max_model_len == 2048  # inherited


def test_deployment_labels_describe_what_differs() -> None:
    cfg = ExperimentConfig.from_dict(
        {
            "infra": {"model": "Qwen/Qwen2.5-0.5B-Instruct"},
            "matrix": [
                {"gpu": "A10G", "engine": {"max_num_seqs": 4}},
                {
                    "gpu": "A100",
                    "gpu_count": 4,
                    "engine": {"max_num_seqs": 16, "tensor_parallel_size": 4},
                },
            ],
        }
    )
    labels = [d.label for d in cfg.deployments()]
    assert labels[0] == "Qwen2.5-0.5B-Instruct-A10G-seqs4"
    assert "A100x4" in labels[1] and "tp4" in labels[1]


def test_indistinguishable_matrix_entries_are_refused() -> None:
    """Two deployments with the same label would file results into the same
    directory and silently overwrite each other."""
    with pytest.raises(ConfigError, match="duplicate labels"):
        ExperimentConfig.from_dict({"matrix": [{"gpu": "A10G"}, {"gpu": "A10G"}]})


def test_every_matrix_entry_is_validated() -> None:
    """An impossible entry must fail at load, not ten minutes into the sweep."""
    with pytest.raises(ConfigError, match="gpu_count"):
        ExperimentConfig.from_dict(
            {"matrix": [{"gpu": "A10G", "gpu_count": 1, "engine": {"tensor_parallel_size": 8}}]}
        )
