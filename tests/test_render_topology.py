"""The topology renderer, and every refusal it owes us.

Most of this file is about what the renderer REFUSES. A config that cannot
produce a working cluster should fail here, in milliseconds, rather than ten
minutes into a weights download on a rented GPU.

The shipped config is exercised too, not just fixtures — it is the one that will
actually be deployed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from infra.render import (
    ConfigError,
    gateway_env,
    hosts_from,
    plan,
    render,
    validate,
    weights_gb,
    workers_from,
)

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "infra" / "config" / "cluster.yaml"


@pytest.fixture
def cfg() -> dict[str, Any]:
    """The real config, deep-copied so a mutation cannot leak between tests."""
    return copy.deepcopy(yaml.safe_load(SHIPPED.read_text()))


# --------------------------------------------------------------------------
# The shipped config
# --------------------------------------------------------------------------


def test_shipped_config_is_deployable(cfg: dict[str, Any]) -> None:
    validate(cfg)


def test_shipped_config_uses_every_gpu_it_claims(cfg: dict[str, Any]) -> None:
    """4 GPUs declared, 4 used. A spare GPU is a topology that is not what it says."""
    host = hosts_from(cfg)["gpu-a"]
    assert sum(w.gpus for w in workers_from(cfg)) == host.gpu_count


def test_disaggregated_gives_two_pools_on_distinct_ports(cfg: dict[str, Any]) -> None:
    workers = workers_from(cfg)
    assert {w.pool for w in workers} == {"prefill", "decode"}
    assert len({w.host_port for w in workers}) == len(workers)


def test_manifests_request_whole_gpus_and_never_a_slice(cfg: dict[str, Any]) -> None:
    """Two slices of one card would masquerade as TP=2 across two cards."""
    text = render(cfg)
    assert "nvidia.com/gpu" in text
    assert "gpumem" not in text
    assert "gpucores" not in text


def test_every_worker_has_a_readiness_probe(cfg: dict[str, Any]) -> None:
    """READY 1/1 lies while torch.compile captures CUDA-graph buckets."""
    docs = [d for d in yaml.safe_load_all(render(cfg)) if d and d["kind"] == "Deployment"]
    assert docs
    for d in docs:
        container = d["spec"]["template"]["spec"]["containers"][0]
        assert container["readinessProbe"]["httpGet"]["path"] == "/v1/models"


def test_engine_flags_appear_once_per_worker(cfg: dict[str, Any]) -> None:
    """The lab hardcoded model and TP twice. That is what this prevents."""
    for w in workers_from(cfg):
        from infra.render import engine_args

        args = engine_args(cfg, w)
        assert args.count("--model") == 1
        assert args.count("--tensor-parallel-size") == 1
        assert args[args.index("--tensor-parallel-size") + 1] == str(w.tp)


def test_prefix_caching_reaches_the_engine(cfg: dict[str, Any]) -> None:
    from infra.render import engine_args

    w = workers_from(cfg)[0]
    assert "--enable-prefix-caching" in engine_args(cfg, w)

    cfg["engine"]["enable_prefix_caching"] = False
    assert "--enable-prefix-caching" not in engine_args(cfg, w)


def test_plan_names_the_host_the_mode_and_the_fit(cfg: dict[str, Any]) -> None:
    out = plan(cfg)
    assert "gpu-a" in out
    assert "disaggregated" in out
    assert "GB/card" in out


# --------------------------------------------------------------------------
# The gateway's environment, which is a contract and not a preference
# --------------------------------------------------------------------------


ENV = {"LAMBDA_HOST_GPU_A": "ubuntu@10.0.0.1", "LAMBDA_HOST_GPU_B": "ubuntu@10.0.0.2"}


def test_disaggregated_env_separates_the_pools(cfg: dict[str, Any]) -> None:
    env = gateway_env(cfg, ENV)
    assert env["LAB_TOPOLOGY"] == "disaggregated"
    assert env["PREFILL_URLS"] != env["DECODE_URLS"]
    assert env["KV_BACKEND"] == "mooncake"


def test_aggregated_env_points_both_lists_at_one_pool(cfg: dict[str, Any]) -> None:
    """pools.py treats equal lists as a single set of engines doing both phases."""
    cfg["topology"] = {
        "mode": "aggregated",
        "pools": {"engine": {"replicas": 2, "tensor_parallel_size": 2, "host": "gpu-a"}},
    }
    env = gateway_env(cfg, ENV)
    assert env["LAB_TOPOLOGY"] == "aggregated"
    assert env["PREFILL_URLS"] == env["DECODE_URLS"]
    assert len(env["PREFILL_URLS"].split(",")) == 2
    assert "KV_BACKEND" not in env


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_pools_must_match_the_mode(cfg: dict[str, Any]) -> None:
    cfg["topology"]["mode"] = "aggregated"  # still has prefill/decode pools
    with pytest.raises(ConfigError, match="needs pools"):
        validate(cfg)


def test_unknown_mode_is_refused(cfg: dict[str, Any]) -> None:
    cfg["topology"]["mode"] = "hybrid"
    with pytest.raises(ConfigError, match="topology.mode must be one of"):
        validate(cfg)


def test_disaggregated_without_a_transport_is_refused(cfg: dict[str, Any]) -> None:
    """Decode engines with no KV to consume."""
    cfg["topology"].pop("kv_transport")
    with pytest.raises(ConfigError, match="needs topology.kv_transport"):
        validate(cfg)


def test_aggregated_with_a_transport_is_refused(cfg: dict[str, Any]) -> None:
    """It would be read nowhere and look configured."""
    cfg["topology"] = {
        "mode": "aggregated",
        "kv_transport": "mooncake",
        "pools": {"engine": {"replicas": 2, "tensor_parallel_size": 2, "host": "gpu-a"}},
    }
    with pytest.raises(ConfigError, match="no hop"):
        validate(cfg)


def test_slicing_is_refused(cfg: dict[str, Any]) -> None:
    cfg["model"]["slicing"] = True
    with pytest.raises(ConfigError, match="slice"):
        validate(cfg)


def test_more_gpus_than_the_host_has_is_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"][0]["gpu"]["count"] = 2  # pools still need 4
    with pytest.raises(ConfigError, match="has 2 GPUs but its pools need 4"):
        validate(cfg)


def test_a_tp_group_may_not_exceed_one_host(cfg: dict[str, Any]) -> None:
    """TP all-reduces every layer. Across Ethernet it is unusable, not merely slow."""
    cfg["hosts"][0]["gpu"]["count"] = 4
    cfg["topology"]["pools"] = {
        "prefill": {"replicas": 1, "tensor_parallel_size": 8, "host": "gpu-a"},
        "decode": {"replicas": 1, "tensor_parallel_size": 1, "host": "gpu-a"},
    }
    with pytest.raises(ConfigError, match="cannot span hosts|pools need"):
        validate(cfg)


def test_a_pool_on_an_undeclared_host_is_refused(cfg: dict[str, Any]) -> None:
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-z"
    with pytest.raises(ConfigError, match="not declared"):
        validate(cfg)


def test_weights_that_do_not_fit_are_refused_before_the_download(
    cfg: dict[str, Any],
) -> None:
    """The mistake this catches was made in this project's own planning.

    72B in bf16 is ~145 GB, so ~72.5 GB a card at TP=2 against ~72 GB usable.
    It does not fit, and without this check the failure is an OOM after a 145 GB
    download.
    """
    cfg["model"]["quantization"] = None
    with pytest.raises(ConfigError, match="does not fit"):
        validate(cfg)


def test_fp8_does_fit_the_same_topology(cfg: dict[str, Any]) -> None:
    """The counterpart: the check is not simply refusing everything at 72B."""
    cfg["model"]["quantization"] = "fp8"
    validate(cfg)


def test_overflow_on_429_is_refused(cfg: dict[str, Any]) -> None:
    cfg["overflow"]["on"] = [429, 503]
    with pytest.raises(ConfigError, match="overspend"):
        validate(cfg)


def test_no_hosts_is_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = []
    with pytest.raises(ConfigError, match="no hosts"):
        validate(cfg)


def test_duplicate_host_names_are_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"].append(copy.deepcopy(cfg["hosts"][0]))
    with pytest.raises(ConfigError, match="duplicate host"):
        validate(cfg)


def test_bad_memory_utilization_is_refused(cfg: dict[str, Any]) -> None:
    cfg["engine"]["gpu_memory_utilization"] = 1.4
    with pytest.raises(ConfigError, match="must be in"):
        validate(cfg)


# --------------------------------------------------------------------------
# Multi-host
# --------------------------------------------------------------------------


def _two_hosts(cfg: dict[str, Any]) -> dict[str, Any]:
    cfg["hosts"].append({"name": "gpu-b", "gpu": {"kind": "H100", "count": 4, "hbm_gb": 80}})
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-b"
    return cfg


def test_pools_split_across_hosts(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    validate(cfg)
    by_host = {w.host.name for w in workers_from(cfg)}
    assert by_host == {"gpu-a", "gpu-b"}


def test_ports_restart_per_host_so_they_do_not_collide(cfg: dict[str, Any]) -> None:
    """Ports are per-machine. Numbering them globally would leave gaps and, worse,
    imply a shared port space that does not exist."""
    cfg = _two_hosts(cfg)
    ports = {w.host.name: w.host_port for w in workers_from(cfg)}
    assert ports == {"gpu-a": 8000, "gpu-b": 8000}


def test_render_can_target_one_host(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    text = render(cfg, host="gpu-b")
    assert "vllm-decode-0" in text
    assert "vllm-prefill-0" not in text


def test_render_refuses_a_host_with_no_pools(cfg: dict[str, Any]) -> None:
    cfg["hosts"].append({"name": "gpu-idle", "gpu": {"count": 4, "hbm_gb": 80}})
    with pytest.raises(ConfigError, match="no pools are placed"):
        render(cfg, host="gpu-idle")


def test_urls_span_hosts(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    env = gateway_env(cfg, ENV)
    assert "10.0.0.2" in env["DECODE_URLS"]
    assert "10.0.0.2" not in env["PREFILL_URLS"]


# --------------------------------------------------------------------------
# The weight estimate itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "quant", "low", "high"),
    [
        ("Qwen/Qwen2.5-72B-Instruct", None, 140, 150),
        ("Qwen/Qwen2.5-72B-Instruct", "fp8", 68, 76),
        ("Qwen/Qwen2.5-7B-Instruct", None, 13, 15),
        ("Qwen/Qwen3.5-4B", "fp8", 3.5, 4.5),
    ],
)
def test_weight_estimate_is_in_the_right_range(
    model: str, quant: str | None, low: float, high: float
) -> None:
    gb = weights_gb(model, quant)
    assert gb is not None
    assert low <= gb <= high


def test_weight_estimate_declines_rather_than_guesses() -> None:
    """A model whose name carries no parameter count returns None, so the fit
    check skips instead of inventing a number to refuse on."""
    assert weights_gb("my-org/some-finetune", "fp8") is None


# --------------------------------------------------------------------------
# Addresses and secrets live in .env, not in the committed config
# --------------------------------------------------------------------------


def test_config_declares_no_address_and_no_key(cfg: dict[str, Any]) -> None:
    """The committed file must not carry an IP or a key path, ever."""
    for host in cfg["hosts"]:
        assert not (set(host) & {"ssh", "ssh_key", "address", "ip"})


def test_an_address_in_the_config_is_refused(cfg: dict[str, Any]) -> None:
    """Refused rather than ignored: ignoring it means someone commits a key path
    and believes it is being used."""
    cfg["hosts"][0]["ssh"] = "ubuntu@1.2.3.4"
    with pytest.raises(ConfigError, match="do not belong"):
        hosts_from(cfg)


def test_a_key_in_the_config_is_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"][0]["ssh_key"] = "~/.ssh/id_ed25519"
    with pytest.raises(ConfigError, match="do not belong"):
        hosts_from(cfg)


def test_unset_address_names_the_variable_to_set(cfg: dict[str, Any]) -> None:
    with pytest.raises(ConfigError, match="LAMBDA_HOST_GPU_A"):
        gateway_env(cfg, {})


def test_lambda_works_for_a_single_host(cfg: dict[str, Any]) -> None:
    """The lab's existing variable keeps working when there is only one box."""
    env = gateway_env(cfg, {"LAMBDA": "ubuntu@10.1.1.5"})
    assert "10.1.1.5" in env["PREFILL_URLS"]


def test_lambda_is_ambiguous_with_two_hosts_and_is_refused(cfg: dict[str, Any]) -> None:
    """Applying one address to both boxes would point every URL at one machine
    while the plan claimed two — a two-worker run that is really one."""
    cfg = _two_hosts(cfg)
    with pytest.raises(ConfigError, match="LAMBDA_HOST_GPU_A"):
        gateway_env(cfg, {"LAMBDA": "ubuntu@10.1.1.5"})


def test_per_host_addresses_reach_the_right_pool(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    env = gateway_env(cfg, ENV)
    assert "10.0.0.1" in env["PREFILL_URLS"]
    assert "10.0.0.2" in env["DECODE_URLS"]


def test_plan_works_with_no_addresses_at_all(cfg: dict[str, Any]) -> None:
    """Planning a topology must not require having rented anything yet."""
    out = plan(cfg)
    assert "unset" in out


def test_overflow_env_comes_from_the_config(cfg: dict[str, Any]) -> None:
    """Emitted, not typed twice. Two copies drift and the environment wins silently."""
    env = gateway_env(cfg, ENV)
    assert env["OVERFLOW_BASE_URL"] == cfg["overflow"]["base_url"]
    assert env["OVERFLOW_MODEL"] == cfg["overflow"]["model"]
    assert env["OVERFLOW_MAX_REQS"] == str(cfg["overflow"]["max_requests"])


def test_the_overflow_api_key_is_never_emitted(cfg: dict[str, Any]) -> None:
    """The one secret in that block. It stays in .env and must not appear in a
    rendered environment, a manifest, or the plan."""
    assert "OVERFLOW_API_KEY" not in gateway_env(cfg, ENV)
    assert "OVERFLOW_API_KEY" not in plan(cfg)
    assert "OVERFLOW_API_KEY" not in render(cfg)


def test_the_config_file_assigns_no_secret() -> None:
    """Checks for an assigned value, not for the words.

    The config mentions OVERFLOW_API_KEY in a comment explaining that it is NOT
    here, which is exactly the kind of text a naive substring search flags. Walk
    the parsed structure instead.
    """
    secretish = {"api_key", "apikey", "token", "secret", "password", "ssh_key"}

    def walk(node: object, path: str = "") -> list[str]:
        found = []
        if isinstance(node, dict):
            for k, v in node.items():
                here = f"{path}.{k}" if path else str(k)
                if str(k).lower() in secretish and v:
                    found.append(here)
                found += walk(v, here)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                found += walk(v, f"{path}[{i}]")
        return found

    assigned = walk(yaml.safe_load(SHIPPED.read_text()))
    assert not assigned, f"secrets assigned in a committed config: {assigned}"
