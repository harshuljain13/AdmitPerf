"""The topology renderer, and every refusal it owes us.

Most of this file is about what the renderer REFUSES. A config that cannot
produce a working cluster should fail here, in milliseconds, rather than ten
minutes into a weights download on a rented GPU.

The shipped config is exercised too, not just fixtures — it is the one that will
actually be deployed.
"""

from __future__ import annotations

import pathlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from infra.render import (
    ConfigError,
    gateway_env,
    hosts_from,
    load,
    plan,
    render,
    validate,
    weights_gb,
    workers_from,
)

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "infra" / "config"
BASE = CONFIG / "base.yaml"
VARIANTS = ("single", "pair", "disagg")


@pytest.fixture
def cfg() -> dict[str, Any]:
    """disagg.yaml, loaded through `extends`.

    The most machinery of the three — two pools, a transport and an endpoint — so a
    refusal test has something to refuse. Tests about the other variants load them
    by name.
    """
    return load(CONFIG / "disagg.yaml")


# --------------------------------------------------------------------------
# The shipped config
# --------------------------------------------------------------------------


def test_shipped_config_is_deployable(cfg: dict[str, Any]) -> None:
    validate(cfg, ENV)


def test_shipped_config_uses_every_gpu_it_claims(cfg: dict[str, Any]) -> None:
    """Every declared GPU is used. A spare card is a topology that is not what it says."""
    hosts = hosts_from(cfg, ENV)
    declared = sum(h.gpu_count for h in hosts.values())
    assert sum(w.gpus for w in workers_from(cfg, ENV)) == declared


def test_disaggregated_gives_two_pools(cfg: dict[str, Any]) -> None:
    workers = workers_from(cfg, ENV)
    assert {w.pool for w in workers} == {"prefill", "decode"}


def test_ports_are_unique_per_host_not_globally(cfg: dict[str, Any]) -> None:
    """Ports are a per-machine resource. With one worker per host they are all
    8000, which is correct — numbering them globally would imply a shared port
    space that does not exist."""
    by_host: dict[str, list[int]] = {}
    for w in workers_from(cfg, ENV):
        by_host.setdefault(w.host.name, []).append(w.host_port)
    for host, ports in by_host.items():
        assert len(ports) == len(set(ports)), host


def test_manifests_request_whole_gpus_and_never_a_slice(cfg: dict[str, Any]) -> None:
    """Two slices of one card would masquerade as TP=2 across two cards."""
    text = render(cfg, env=ENV)
    assert "nvidia.com/gpu" in text
    assert "gpumem" not in text
    assert "gpucores" not in text


def test_every_worker_has_a_readiness_probe(cfg: dict[str, Any]) -> None:
    """READY 1/1 lies while torch.compile captures CUDA-graph buckets."""
    docs = [d for d in yaml.safe_load_all(render(cfg, env=ENV)) if d and d["kind"] == "Deployment"]
    assert docs
    for d in docs:
        container = d["spec"]["template"]["spec"]["containers"][0]
        assert container["readinessProbe"]["httpGet"]["path"] == "/v1/models"


def test_engine_flags_appear_once_per_worker(cfg: dict[str, Any]) -> None:
    """The lab hardcoded model and TP twice. That is what this prevents."""
    for w in workers_from(cfg, ENV):
        from infra.render import engine_args

        args = engine_args(cfg, w)
        assert args.count("--model") == 1
        assert args.count("--tensor-parallel-size") == 1
        assert args[args.index("--tensor-parallel-size") + 1] == str(w.tp)


def test_prefix_caching_reaches_the_engine(cfg: dict[str, Any]) -> None:
    from infra.render import engine_args

    w = workers_from(cfg, ENV)[0]
    assert "--enable-prefix-caching" in engine_args(cfg, w)

    cfg["engine"]["enable_prefix_caching"] = False
    assert "--enable-prefix-caching" not in engine_args(cfg, w)


def test_plan_names_the_host_the_mode_and_the_fit(cfg: dict[str, Any]) -> None:
    out = plan(cfg, ENV)
    # gpu-1, because the shipped config derives names from the inventory order.
    assert "gpu-1" in out
    assert "disaggregated" in out
    assert "GB/card" in out


# --------------------------------------------------------------------------
# The gateway's environment, which is a contract and not a preference
# --------------------------------------------------------------------------


#: One environment for every test: a public address and a private one per host,
#: because the renderer needs both and a test that supplies only one is testing a
#: configuration nobody would deploy.
ENV = (
    {f"LAMBDA_HOST_{i}": f"ubuntu@10.0.0.{i}" for i in range(1, 9)}
    | {f"LAMBDA_PRIVATE_{i}": f"10.19.80.{i}" for i in range(1, 9)}
    | {"LAMBDA_HOST_GPU_A": "ubuntu@10.0.0.1", "LAMBDA_HOST_GPU_B": "ubuntu@10.0.0.2"}
)


def fleet(
    cfg: dict[str, Any], n: int, *, kind: str = "A100", count: int = 1, hbm: float = 40
) -> dict[str, Any]:
    """Declare n identical hosts, named gpu-1..gpu-n.

    Tests declare the fleet they need instead of inheriting the shipped one. That
    inheritance has broken this file four times; each break taught nothing about
    the code, only that the deployment had changed.
    """
    cfg["hosts"] = [
        {
            "name": f"gpu-{i}",
            "address_env": f"LAMBDA_HOST_{i}",
            "gpu": {"kind": kind, "count": count, "hbm_gb": hbm},
        }
        for i in range(1, n + 1)
    ]
    return cfg


#: A single-host inventory, for the tests that only need the shipped shape.
ENV_ONE = {"LAMBDA_HOSTS": "ubuntu@10.0.0.1"}


def named(
    cfg: dict[str, Any], *names: str, kind: str = "A100", cards: int = 4, hbm: float = 40
) -> dict[str, Any]:
    """Switch the config to the EXPLICIT list form with these host names.

    The address-resolution tests are about named hosts and the LAMBDA fallback, so
    they have to declare named hosts. The shipped config uses the inventory form.
    """
    cfg["hosts"] = [
        {"name": n, "gpu": {"kind": kind, "count": cards, "hbm_gb": hbm}} for n in names
    ]
    return cfg


def pin(
    cfg: dict[str, Any],
    *,
    kind: str = "H100",
    cards: int = 4,
    hbm: float = 80,
    model: str | None = None,
    quant: str | None = "__keep__",
    tp: int = 1,
    replicas: int = 2,
) -> dict[str, Any]:
    """State the whole scenario explicitly.

    Four tests broke when the shipped topology changed, because they asserted
    numbers they had inherited rather than numbers they had declared. A test that
    moves with the config is measuring today's deployment, not the code.
    """
    # Always the EXPLICIT list form. A test asserting a specific shape should
    # declare that shape, not inherit whatever the shipped config happens to use —
    # the shipped one is an inventory now, and a test that follows it is testing
    # today's deployment rather than the code.
    cfg["hosts"] = [{"name": "gpu-a", "gpu": {"kind": kind, "count": cards, "hbm_gb": hbm}}]
    if model:
        cfg["model"]["id"] = model
    if quant != "__keep__":
        cfg["model"]["quantization"] = quant
    cfg["topology"]["pools"] = {
        pool: {"replicas": replicas, "tensor_parallel_size": tp, "host": "gpu-a"}
        for pool in ("prefill", "decode")
    }
    return cfg


def test_disaggregated_env_separates_the_pools(cfg: dict[str, Any]) -> None:
    env = gateway_env(cfg, ENV)
    assert env["LAB_TOPOLOGY"] == "disaggregated"
    assert env["PREFILL_URLS"] != env["DECODE_URLS"]
    assert env["KV_BACKEND"] == "mooncake"


def test_aggregated_env_points_both_lists_at_one_pool(cfg: dict[str, Any]) -> None:
    """pools.py treats equal lists as a single set of engines doing both phases."""
    fleet(cfg, 2, count=2)
    cfg["topology"] = {
        "mode": "aggregated",
        "pools": {"engine": {"replicas": 2, "tensor_parallel_size": 2}},
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
        validate(cfg, ENV)


def test_unknown_mode_is_refused(cfg: dict[str, Any]) -> None:
    cfg["topology"]["mode"] = "hybrid"
    with pytest.raises(ConfigError, match="topology.mode must be one of"):
        validate(cfg, ENV)


def test_disaggregated_without_a_transport_is_refused(cfg: dict[str, Any]) -> None:
    """Decode engines with no KV to consume."""
    cfg["topology"].pop("kv_transport")
    with pytest.raises(ConfigError, match="needs topology.kv_transport"):
        validate(cfg, ENV)


def test_aggregated_with_a_transport_is_refused(cfg: dict[str, Any]) -> None:
    """It would be read nowhere and look configured."""
    cfg["topology"] = {
        "mode": "aggregated",
        "kv_transport": "mooncake",
        "pools": {"engine": {"replicas": 2, "tensor_parallel_size": 2, "host": "gpu-a"}},
    }
    with pytest.raises(ConfigError, match="no hop"):
        validate(cfg, ENV)


def test_slicing_is_refused(cfg: dict[str, Any]) -> None:
    cfg["model"]["slicing"] = True
    with pytest.raises(ConfigError, match="slice"):
        validate(cfg, ENV)


def test_more_gpus_than_the_host_has_is_refused(cfg: dict[str, Any]) -> None:
    pin(cfg, cards=2, kind="A100", hbm=40)  # pools still need 4
    with pytest.raises(ConfigError, match="has 2 GPUs but its pools need 4"):
        validate(cfg, ENV)


def test_a_tp_group_may_not_exceed_one_host(cfg: dict[str, Any]) -> None:
    """TP all-reduces every layer. Across Ethernet it is unusable, not merely slow."""
    pin(cfg, cards=4, kind="A100", hbm=40)
    cfg["topology"]["pools"] = {
        "prefill": {"replicas": 1, "tensor_parallel_size": 8, "host": "gpu-a"},
        "decode": {"replicas": 1, "tensor_parallel_size": 1, "host": "gpu-a"},
    }
    with pytest.raises(ConfigError, match="cannot span hosts|pools need"):
        validate(cfg, ENV)


def test_a_pool_on_an_undeclared_host_is_refused(cfg: dict[str, Any]) -> None:
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-z"
    with pytest.raises(ConfigError, match="not declared"):
        validate(cfg, ENV)


def test_weights_that_do_not_fit_are_refused_before_the_download(
    cfg: dict[str, Any],
) -> None:
    """The mistake this catches was made in this project's own planning.

    72B in bf16 is ~144 GB, so ~72 GB a card at TP=2 against ~72 GB usable. It
    does not fit with room to serve, and without this check the failure is an OOM
    after a 144 GB download.

    The hardware and model are set explicitly rather than inherited from the
    shipped config. A test that moves when the config moves is asserting
    something about today's deployment, not about the check.
    """
    pin(cfg, model="Qwen/Qwen2.5-72B-Instruct", quant=None, tp=2, replicas=1)
    with pytest.raises(ConfigError, match="does not fit"):
        validate(cfg, ENV)


def test_fp8_does_fit_the_same_topology(cfg: dict[str, Any]) -> None:
    """The counterpart: the check is not simply refusing everything at 72B."""
    pin(cfg, model="Qwen/Qwen2.5-72B-Instruct", quant="fp8", tp=2, replicas=1)
    validate(cfg, ENV)


def test_the_shipped_shape_gives_a_hop_and_a_placement_decision(
    cfg: dict[str, Any],
) -> None:
    """Both, which is the only reason the shipped topology is shaped this way.

    Disaggregated gives the KV hop; two replicas per pool give placement
    somewhere to choose. At TP=2 there would be one replica per pool and
    placement would have a single destination, which is not a decision.
    """
    validate(cfg, ENV)
    assert cfg["topology"]["mode"] == "disaggregated"
    assert cfg["topology"]["kv_transport"]
    for pool in ("prefill", "decode"):
        assert cfg["topology"]["pools"][pool]["replicas"] > 1


def test_32b_fp8_fits_one_h100_with_room_to_serve(cfg: dict[str, Any]) -> None:
    """~32 GB of weights against ~72 GB usable leaves ~40 GB of KV, about five
    sequences at 32k — scarce enough that admission bites, which is the point."""
    pin(cfg, quant="fp8")
    validate(cfg, ENV)


def test_overflow_on_429_is_refused(cfg: dict[str, Any]) -> None:
    cfg["overflow"]["on"] = [429, 503]
    with pytest.raises(ConfigError, match="overspend"):
        validate(cfg, ENV)


def test_no_hosts_is_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = []  # neither a list nor an inventory
    with pytest.raises(ConfigError, match="no hosts"):
        validate(cfg, ENV)


def test_duplicate_host_names_are_refused(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = [
        {"name": "gpu-a", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}},
        {"name": "gpu-a", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}},
    ]
    with pytest.raises(ConfigError, match="duplicate host"):
        validate(cfg, ENV)


def test_bad_memory_utilization_is_refused(cfg: dict[str, Any]) -> None:
    cfg["engine"]["gpu_memory_utilization"] = 1.4
    with pytest.raises(ConfigError, match="must be in"):
        validate(cfg, ENV)


# --------------------------------------------------------------------------
# Multi-host
# --------------------------------------------------------------------------


def _two_hosts(cfg: dict[str, Any]) -> dict[str, Any]:
    """Two NAMED hosts, one pool pinned to each.

    The explicit list form, because these tests are about named hosts and the
    LAMBDA fallback. The shipped config derives its hosts from an inventory.
    """
    named(cfg, "gpu-a", "gpu-b")
    cfg["topology"]["pools"]["prefill"]["host"] = "gpu-a"
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-b"
    return cfg


def test_pools_split_across_hosts(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    validate(cfg, ENV)
    by_host = {w.host.name for w in workers_from(cfg, ENV)}
    assert by_host == {"gpu-a", "gpu-b"}


def test_ports_restart_per_host_so_they_do_not_collide(cfg: dict[str, Any]) -> None:
    """Ports are per-machine. Numbering them globally would leave gaps and, worse,
    imply a shared port space that does not exist.

    Collected per host as a list: keying by host name alone let later replicas
    overwrite earlier ones, so the assertion passed for the wrong reason while
    there was one replica per pool.
    """
    cfg = _two_hosts(cfg)
    ports: dict[str, list[int]] = {}
    for w in workers_from(cfg, ENV):
        ports.setdefault(w.host.name, []).append(w.host_port)
    for host, got in ports.items():
        assert got == list(range(8000, 8000 + len(got))), host
    assert min(ports["gpu-a"]) == min(ports["gpu-b"]) == 8000


def test_render_can_target_one_host(cfg: dict[str, Any]) -> None:
    cfg = _two_hosts(cfg)
    text = render(cfg, host="gpu-b", env=ENV)
    assert "vllm-decode-0" in text
    assert "vllm-prefill-0" not in text


def test_render_refuses_a_host_with_no_pools(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = [
        {"name": "gpu-a", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}},
        {"name": "gpu-idle", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}},
    ]
    cfg["topology"]["pools"]["prefill"]["host"] = "gpu-a"
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-a"
    with pytest.raises(ConfigError, match="no pools are placed"):
        render(cfg, host="gpu-idle", env=ENV)


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
    named(cfg, "gpu-a")
    cfg["hosts"][0]["ssh"] = "ubuntu@1.2.3.4"
    with pytest.raises(ConfigError, match="do not belong"):
        hosts_from(cfg, ENV)


def test_a_key_in_the_config_is_refused(cfg: dict[str, Any]) -> None:
    named(cfg, "gpu-a")
    cfg["hosts"][0]["ssh_key"] = "~/.ssh/id_ed25519"
    with pytest.raises(ConfigError, match="do not belong"):
        hosts_from(cfg, ENV)


def test_unset_address_names_the_variable_to_set(cfg: dict[str, Any]) -> None:
    named(cfg, "gpu-a")
    with pytest.raises(ConfigError, match="LAMBDA_HOST_GPU_A"):
        gateway_env(cfg, {})


def test_lambda_works_for_a_single_host(cfg: dict[str, Any]) -> None:
    """The lab's existing variable keeps working when there is only one box."""
    named(cfg, "gpu-a")
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
    """Planning a topology must not require having rented anything yet.

    The empty env is passed explicitly. Letting this fall through to the real
    environment made the test pass or fail depending on whether the developer
    running it happened to have a .env — the assertion was about the machine, not
    the code.
    """
    out = plan(cfg, {})
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
    assert "OVERFLOW_API_KEY" not in plan(cfg, ENV)
    assert "OVERFLOW_API_KEY" not in render(cfg, env=ENV)


def test_no_config_file_assigns_a_secret() -> None:
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

    for name in ("base", *VARIANTS):
        found = walk(yaml.safe_load((CONFIG / f"{name}.yaml").read_text()))
        assert not found, f"secrets assigned in {name}.yaml: {found}"


# --------------------------------------------------------------------------
# What the silicon can actually execute, and how much fits
# --------------------------------------------------------------------------


def test_fp8_on_an_a100_is_refused(cfg: dict[str, Any]) -> None:
    """A100 is sm80. fp8 tensor cores arrived with Hopper and Ada, so an fp8
    checkpoint fails after the weights download — the most expensive way to
    learn it."""
    cfg["hosts"] = [{"name": "gpu-a", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}}]
    cfg["model"]["quantization"] = "fp8"
    with pytest.raises(ConfigError, match="compute capability"):
        validate(cfg, ENV)


def test_fp8_on_an_h100_is_allowed(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = [{"name": "gpu-a", "gpu": {"kind": "H100", "count": 4, "hbm_gb": 80}}]
    cfg["model"]["quantization"] = "fp8"
    validate(cfg, ENV)


def test_int8_on_an_a100_is_allowed(cfg: dict[str, Any]) -> None:
    cfg["hosts"] = [{"name": "gpu-a", "gpu": {"kind": "A100", "count": 4, "hbm_gb": 40}}]
    cfg["model"]["quantization"] = "int8"
    validate(cfg, ENV)


def test_an_unknown_card_does_not_block_a_quantization(cfg: dict[str, Any]) -> None:
    """Refusing what we cannot verify would make the config unusable on new
    hardware. The fit check still applies."""
    cfg["hosts"] = [{"name": "gpu-a", "gpu": {"kind": "B200", "count": 4, "hbm_gb": 80}}]
    cfg["model"]["quantization"] = "fp8"
    validate(cfg, ENV)


def test_kv_bytes_per_token_follows_the_attention_shape(cfg: dict[str, Any]) -> None:
    """2 x layers x kv_heads x head_dim x dtype_bytes, read from the config.

    Deriving the expectation from the config rather than hardcoding today's model
    keeps this a test of the arithmetic. The previous version asserted the 32B
    numbers and broke the moment the model changed, which told us nothing.
    """
    from infra.render import kv_bytes_per_token

    a = cfg["model"]["attention"]
    expected = 2 * a["layers"] * a["kv_heads"] * a["head_dim"] * a["dtype_bytes"]
    assert kv_bytes_per_token(cfg) == expected


def test_kv_estimate_declines_without_a_declared_shape(cfg: dict[str, Any]) -> None:
    from infra.render import kv_bytes_per_token

    cfg["model"].pop("attention")
    assert kv_bytes_per_token(cfg) is None


def test_plan_says_whether_kv_or_the_scheduler_binds(cfg: dict[str, Any]) -> None:
    """The sentence that decides whether a run can prove anything.

    If max_num_seqs caps concurrency below what KV allows, the scheduler refuses
    before the cache does and a KV-pressure policy never fires. That is exactly
    how half-capacity-headroom produced three runs of flat signals.
    """
    out = plan(cfg, ENV)
    assert "KV binds" in out

    cfg["engine"]["max_num_seqs"] = 1
    assert "SCHEDULER binds" in plan(cfg, ENV)


def test_a_config_that_holds_fewer_than_one_sequence_is_refused(
    cfg: dict[str, Any],
) -> None:
    """The gap a comparison table exposed in the fit check itself.

    32B int8 on a single 40 GB card leaves 4 GB of KV. That is 11% of usable, so
    it cleared the 5% floor — and it holds 0.12 sequences at 32k. The engine
    starts and then refuses every request, which is the failure the fit check
    existed to prevent and did not.
    """
    pin(
        cfg,
        kind="A100",
        cards=2,
        hbm=40,
        model="Qwen/Qwen2.5-32B-Instruct",
        quant="int8",
        tp=1,
        replicas=1,
    )
    cfg["model"]["attention"] = {
        "layers": 64,
        "kv_heads": 8,
        "head_dim": 128,
        "dtype_bytes": 2,
    }
    with pytest.raises(ConfigError, match="Fewer than one"):
        validate(cfg, ENV)


def test_the_same_shape_passes_once_it_can_hold_a_sequence(cfg: dict[str, Any]) -> None:
    """int4 on the same two cards leaves 20 GB and holds ~2 sequences."""
    pin(cfg, kind="A100", cards=2, hbm=40, quant="awq", tp=1, replicas=1)
    validate(cfg, ENV)


# --------------------------------------------------------------------------
# address_env: the config names the variable, .env holds the address
# --------------------------------------------------------------------------


def test_each_host_reads_the_variable_the_config_names(cfg: dict[str, Any]) -> None:
    fleet(cfg, 3)
    hosts = hosts_from(cfg, ENV)
    assert [h.address(ENV) for h in hosts.values()] == [
        "ubuntu@10.0.0.1",
        "ubuntu@10.0.0.2",
        "ubuntu@10.0.0.3",
    ]


def test_hosts_may_differ_in_shape(cfg: dict[str, Any]) -> None:
    """A fleet is not necessarily uniform, which is why every host declares its
    own kind, count and HBM rather than sharing one template."""
    cfg["hosts"] = [
        {
            "name": "big",
            "address_env": "LAMBDA_HOST_1",
            "gpu": {"kind": "A100", "count": 4, "hbm_gb": 80},
        },
        {
            "name": "small",
            "address_env": "LAMBDA_HOST_2",
            "gpu": {"kind": "A10", "count": 1, "hbm_gb": 24},
        },
    ]
    hosts = hosts_from(cfg, ENV)
    assert hosts["big"].gpu_count == 4
    assert hosts["small"].hbm_gb == 24


def test_an_unset_address_names_the_variable_from_the_config(cfg: dict[str, Any]) -> None:
    """The error has to name the variable the config asked for, not a derived one."""
    fleet(cfg, 4)
    with pytest.raises(ConfigError, match="LAMBDA_HOST_1"):
        gateway_env(cfg, {})


def test_a_host_without_address_env_falls_back_to_its_name(cfg: dict[str, Any]) -> None:
    """So the shorter form still works for a single box."""
    named(cfg, "gpu-a")
    assert hosts_from(cfg, ENV)["gpu-a"].address(ENV) == "ubuntu@10.0.0.1"


# --------------------------------------------------------------------------
# Automatic placement
# --------------------------------------------------------------------------


def test_spare_hosts_are_reported_as_idle(cfg: dict[str, Any]) -> None:
    """Adding a host does not silently add workers. Capacity that appeared without
    anyone asking would change what a run measured."""
    fleet(cfg, 2, count=4)
    out = plan(cfg, ENV)
    assert "using 0/4" in out
    assert "no pools placed here" in out


def test_replicas_spread_across_hosts(cfg: dict[str, Any]) -> None:
    """Placement is PER REPLICA, not per pool. A replica is an independent server,
    so six of them may use two four-GPU boxes. Placing per pool refused that, and
    it is a real topology."""
    fleet(cfg, 2, count=4)
    cfg["topology"]["pools"]["prefill"]["replicas"] = 6
    cfg["topology"]["pools"]["decode"]["replicas"] = 2
    by_host: dict[str, list[str]] = {}
    for w in workers_from(cfg, ENV):
        by_host.setdefault(w.host.name, []).append(w.name)
    assert len(by_host) == 2
    assert sum(len(v) for v in by_host.values()) == 8


def test_placement_is_deterministic(cfg: dict[str, Any]) -> None:
    """Two renders of the same config and inventory must assign identically, or a
    rerun measures a different deployment while claiming to be a repeat."""
    fleet(cfg, 2, count=4)
    env = ENV
    first = [(w.name, w.host.name, w.host_port) for w in workers_from(cfg, env)]
    second = [(w.name, w.host.name, w.host_port) for w in workers_from(cfg, env)]
    assert first == second


def test_a_pinned_pool_stays_where_it_is_pinned(cfg: dict[str, Any]) -> None:
    fleet(cfg, 2, count=4)
    cfg["topology"]["pools"]["decode"]["host"] = "gpu-2"
    placed = {
        w.pool: {x.host.name for x in workers_from(cfg, ENV) if x.pool == w.pool}
        for w in workers_from(cfg, ENV)
    }
    assert placed["decode"] == {"gpu-2"}


def test_placement_works_around_a_pinned_pool(cfg: dict[str, Any]) -> None:
    """The pinned pool claims its GPUs first, so an unpinned pool is not handed
    cards the pinned one is about to need."""
    fleet(cfg, 2, count=4)
    cfg["topology"]["pools"]["prefill"]["replicas"] = 4
    cfg["topology"]["pools"]["prefill"]["host"] = "gpu-1"
    cfg["topology"]["pools"]["decode"]["replicas"] = 4
    hosts = {w.host.name for w in workers_from(cfg, ENV) if w.pool == "decode"}
    assert hosts == {"gpu-2"}


def test_too_few_gpus_for_an_unpinned_pool_is_refused(cfg: dict[str, Any]) -> None:
    fleet(cfg, 2, count=4)
    cfg["topology"]["pools"]["prefill"]["replicas"] = 9
    with pytest.raises(ConfigError, match="no host has"):
        workers_from(cfg, ENV)


def test_the_plan_shows_which_host_each_worker_landed_on(cfg: dict[str, Any]) -> None:
    """An automatic assignment that is not printed is an assignment nobody can
    check against the run it produced."""
    fleet(cfg, 2, count=4)
    out = plan(cfg, ENV)
    for line in out.splitlines():
        if "vllm-" in line:
            assert line.startswith("    ")
    assert "gpu-1" in out and "gpu-2" in out


# --------------------------------------------------------------------------
# The transport's endpoint: KV_BACKEND selects, each backend configures itself
# --------------------------------------------------------------------------


def test_the_endpoint_is_emitted_so_it_cannot_be_unset(cfg: dict[str, Any]) -> None:
    """MOONCAKE_URL unset makes _store_put return without posting and log nothing,
    so every hop becomes a no-op and zero hops reads as a measurement."""
    assert gateway_env(cfg, ENV)["MOONCAKE_URL"] == cfg["topology"]["kv_endpoint"]


def test_mooncake_without_an_endpoint_is_refused(cfg: dict[str, Any]) -> None:
    cfg["topology"].pop("kv_endpoint")
    with pytest.raises(ConfigError, match="silent no-op"):
        validate(cfg, ENV)


def test_an_endpoint_on_a_backend_that_has_no_address_is_refused(
    cfg: dict[str, Any],
) -> None:
    """nccl is a collective and nixl is an RDMA path. Neither has a URL, so one
    set against them would be read by nothing while looking configured — which is
    why this is not a generic KV_BACKEND_URL."""
    cfg["topology"]["kv_transport"] = "nccl"
    with pytest.raises(ConfigError, match="not URL-addressed"):
        validate(cfg, ENV)


def test_aggregated_emits_no_transport_and_no_endpoint(cfg: dict[str, Any]) -> None:
    fleet(cfg, 2, count=2)
    cfg["topology"] = {
        "mode": "aggregated",
        "pools": {"engine": {"replicas": 2, "tensor_parallel_size": 2}},
    }
    env = gateway_env(cfg, ENV)
    assert "KV_BACKEND" not in env
    assert "MOONCAKE_URL" not in env


# --------------------------------------------------------------------------
# extends: three runnable configs, one shared base
# --------------------------------------------------------------------------

#: Kept as an alias so the extends tests read clearly.
FULL = ENV


@pytest.mark.parametrize(
    ("name", "workers", "mode"),
    [("single", 1, "aggregated"), ("pair", 2, "aggregated"), ("disagg", 4, "disaggregated")],
)
def test_each_config_renders_the_fleet_it_describes(name: str, workers: int, mode: str) -> None:
    cfg = load(CONFIG / f"{name}.yaml")
    validate(cfg, FULL)
    assert len(workers_from(cfg, FULL)) == workers
    assert cfg["topology"]["mode"] == mode
    assert cfg["config_name"] == name


def test_the_variants_share_everything_except_topology() -> None:
    """The property that makes two runs comparable. If a variant could change the
    model or the engine flags, "only the topology changed" would be a claim rather
    than a fact."""
    loaded = [load(CONFIG / f"{n}.yaml") for n in VARIANTS]
    for key in ("hosts", "model", "engine", "overflow", "admission", "placement"):
        assert all(c[key] == loaded[0][key] for c in loaded), key


def test_the_base_declares_no_topology() -> None:
    """It is the one thing each variant changes, so inheriting a default would let a
    variant silently run the wrong shape."""
    assert "topology" not in yaml.safe_load(BASE.read_text())


def test_base_is_not_renderable_on_its_own() -> None:
    with pytest.raises(KeyError):
        validate(load(BASE), FULL)


def test_a_variant_may_override_one_shared_value_without_losing_the_rest(
    tmp_path: pathlib.Path,
) -> None:
    """Deep merge, so a short-context variant does not have to restate the engine."""
    (tmp_path / "base.yaml").write_text(BASE.read_text())
    (tmp_path / "v.yaml").write_text(
        "extends: base.yaml\n"
        "engine: {max_model_len: 8192}\n"
        "topology: {mode: aggregated, pools: {engine: {replicas: 1, tensor_parallel_size: 1}}}\n"
    )
    cfg = load(tmp_path / "v.yaml")
    assert cfg["engine"]["max_model_len"] == 8192
    assert cfg["engine"]["gpu_memory_utilization"] == 0.90  # inherited


def test_pools_replace_rather_than_merge(tmp_path: pathlib.Path) -> None:
    """Inheriting half a pool set is not a topology. An aggregated variant extending
    a disaggregated parent must not end up with prefill, decode AND engine."""
    (tmp_path / "base.yaml").write_text(
        BASE.read_text() + "\ntopology:\n  mode: disaggregated\n  split: phase\n"
        "  kv_transport: mooncake\n  kv_endpoint: http://10.19.0.1:50051\n"
        "  pools:\n    prefill: {replicas: 1, tensor_parallel_size: 1}\n"
        "    decode: {replicas: 1, tensor_parallel_size: 1}\n"
    )
    (tmp_path / "v.yaml").write_text(
        "extends: base.yaml\n"
        "topology: {mode: aggregated, pools: {engine: {replicas: 1, tensor_parallel_size: 1}}}\n"
    )
    assert set(load(tmp_path / "v.yaml")["topology"]["pools"]) == {"engine"}


def test_a_missing_parent_is_named(tmp_path: pathlib.Path) -> None:
    (tmp_path / "v.yaml").write_text("extends: nope.yaml\n")
    with pytest.raises(ConfigError, match="does not exist"):
        load(tmp_path / "v.yaml")


def test_a_cycle_is_refused(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n")
    with pytest.raises(ConfigError, match="cycle"):
        load(tmp_path / "a.yaml")


def test_the_plan_names_the_config() -> None:
    """A rendered run must say which variant produced it, or two bundles are
    indistinguishable."""
    assert "config=pair" in plan(load(CONFIG / "pair.yaml"), FULL)


# --------------------------------------------------------------------------
# Public for SSH, private for the data plane — different networks
# --------------------------------------------------------------------------


def test_worker_urls_use_the_private_address(cfg: dict[str, Any]) -> None:
    """Lambda firewalls the public interface down to port 22, so a URL built from
    it connects to nothing — and fails as a timeout mid-run rather than an error at
    configuration time."""
    env = gateway_env(cfg, FULL)
    assert "10.19.80." in env["PREFILL_URLS"]
    assert "150.136." not in env["PREFILL_URLS"]


def test_ssh_still_uses_the_public_address(cfg: dict[str, Any]) -> None:
    """The two are not interchangeable in either direction: the private network is
    unreachable from a laptop."""
    host = hosts_from(cfg, FULL)["gpu-1"]
    assert host.address(FULL) == "ubuntu@10.0.0.1"  # LAMBDA_HOST_1 in the test env
    assert host.data_address(FULL) == "10.19.80.1"


def test_a_host_without_a_private_address_falls_back_to_public(
    cfg: dict[str, Any],
) -> None:
    """Correct for a single box reached through a tunnel, which is how the lab ran."""
    fleet(cfg, 1)  # no private_env
    host = hosts_from(cfg, FULL)["gpu-1"]
    assert host.data_address(FULL) == host.address(FULL)


def test_the_kv_endpoint_is_a_private_address() -> None:
    """Mooncake lives on one box; the other three reach it over the private
    network or not at all."""
    endpoint = load(CONFIG / "disagg.yaml")["topology"]["kv_endpoint"]
    assert "10.19." in endpoint, endpoint
