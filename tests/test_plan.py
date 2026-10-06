"""`make plan` — whether a run on a cluster can mean anything.

This replaces tests/test_render_topology.py. That file tested infra/render.py, which
generated Kubernetes manifests, resolved hosts and listed packages; Helm templates do
all of that now and `helm template` refuses the bad combinations (see
infra/scripts/check-refusals.sh, which this file also runs).

What is left is the part no deployment tool does: the arithmetic that decides whether
an experiment is worth running. Every assertion here is about a config that deploys
cleanly, comes up healthy, and produces numbers that mean nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
sys.path.insert(0, str(INFRA))

plan = pytest.importorskip("plan", reason="infra/plan.py")

DEFAULTS = INFRA / "chart" / "values.yaml"
VALUES = INFRA / "values"
A100 = {"hbm_gb": 40.0, "gpu_kind": "A100"}


@pytest.fixture
def cfg() -> dict[str, Any]:
    return plan.load(VALUES / "single.yaml", DEFAULTS)


def topologies() -> list[Path]:
    return sorted(VALUES.glob("*.yaml"))


# --------------------------------------------------------------------------
# Every shipped topology plans
# --------------------------------------------------------------------------


@pytest.mark.parametrize("values", topologies(), ids=lambda p: p.stem)
def test_every_topology_plans(values: Path) -> None:
    """The shipped files are the ones that will be deployed, so they are exercised
    rather than fixtures alone."""
    cfg = plan.load(values, DEFAULTS)
    text = plan.text(cfg, values.stem, **A100)
    assert values.stem in text
    assert "WHAT RUNS" in text


def test_values_override_defaults_the_way_helm_merges_them() -> None:
    """A values file is a partial overlay. If the merge were a replace, a topology
    declaring only `pools` would lose every engine flag and the plan would describe a
    cluster nobody could deploy."""
    cfg = plan.load(VALUES / "disagg.yaml", DEFAULTS)
    assert cfg["engine"]["maxNumSeqs"] == 64  # from defaults
    assert cfg["topology"]["mode"] == "disaggregated"  # from the overlay
    assert len(cfg["pools"]) == 2


# --------------------------------------------------------------------------
# The KV arithmetic, which is the whole point
# --------------------------------------------------------------------------


def test_kv_bytes_per_token_is_2_x_layers_x_kv_heads_x_head_dim_x_dtype(
    cfg: dict[str, Any],
) -> None:
    """Grouped-query attention keeps kv_heads far below the attention head count,
    which is why this comes out smaller than people expect."""
    a = cfg["model"]["attention"]
    expected = 2 * a["layers"] * a["kvHeads"] * a["headDim"] * a["dtypeBytes"]
    assert plan.kv_bytes_per_token(cfg) == expected
    # 56 KiB/token for Qwen2.5-7B. A wrong figure here moves the crossover, and the
    # crossover is the number that decides whether to rent the hardware.
    assert plan.kv_bytes_per_token(cfg) == 57344


def test_a_model_with_no_declared_shape_declines_rather_than_guessing(
    cfg: dict[str, Any],
) -> None:
    """A guessed cache size is worse than none: the plan would state a crossover it
    cannot support and the run would be INERT for a reason nobody checked."""
    cfg["model"].pop("attention")
    assert plan.kv_bytes_per_token(cfg) is None
    assert "crossover" not in plan.text(cfg, "single", **A100)


def test_the_crossover_is_the_pool_divided_by_the_sequence_cap(cfg: dict[str, Any]) -> None:
    """The cache fills with maxNumSeqs x tokensPerRequest, so the shortest request at
    which KV binds is pool / maxNumSeqs."""
    text = plan.text(cfg, "single", **A100)
    pool_k = float(text.split("=  ")[1].split("k tokens")[0])
    crossover_k = float(text.split("crossover   ~")[1].split("k tokens")[0])
    assert crossover_k == pytest.approx(pool_k / cfg["engine"]["maxNumSeqs"], rel=0.02)


def test_the_plan_does_not_claim_to_know_which_constraint_binds(cfg: dict[str, Any]) -> None:
    """It cannot: the request length lives in the experiment, not in the config.

    An earlier version did this arithmetic at max_model_len and announced "KV binds —
    admission can bite" whenever seqs-at-full-context fell under maxNumSeqs. The
    shipped workload sent 2168 tokens against a 32768 context, so the real ceiling on
    kv_used_fraction was 0.35 and a 0.90 threshold was unreachable — while the plan
    said admission could bite.
    """
    text = plan.text(cfg, "single", **A100)
    assert "admission can bite" not in text
    # It reports the crossover and labels each probe instead.
    assert "crossover" in text
    assert "KV binds" in text
    assert "scheduler binds" in text


def test_a_workload_below_the_crossover_is_called_inert(cfg: dict[str, Any]) -> None:
    """The failure this whole section exists to catch. Below the crossover the
    scheduler cap fills first, kv_used_fraction cannot reach a high threshold at any
    arrival rate, and the run reports numbers indistinguishable from no policy."""
    text = plan.text(cfg, "single", **A100)
    short = [ln for ln in text.splitlines() if "2048 +" in ln]
    assert short and "INERT" in short[0]
    assert "at ANY arrival rate" in text


def test_a_bigger_sequence_cap_lowers_the_crossover(cfg: dict[str, Any]) -> None:
    """maxNumSeqs is the single flag that decides whether a KV policy can fire, which
    is why omitting it (as the old lambda_vllm.sh did) invalidated the plan."""

    def crossover(cap: int) -> float:
        cfg["engine"]["maxNumSeqs"] = cap
        text = plan.text(cfg, "single", **A100)
        return float(text.split("crossover   ~")[1].split("k tokens")[0])

    assert crossover(256) < crossover(64) < crossover(16)


# --------------------------------------------------------------------------
# Slices are not cards
# --------------------------------------------------------------------------


def test_a_slice_caps_the_memory_an_engine_can_see() -> None:
    """Computing from the whole card overstated the cache by the slice factor — 30 GB
    of KV on a 16 GiB slice — which is the same class of error as doing the arithmetic
    at max_model_len instead of at the workload's length."""
    cfg = plan.load(VALUES / "sliced.yaml", DEFAULTS)
    assert cfg["slicing"]["enabled"] is True
    visible = plan.visible_gb(cfg, 40.0)
    assert visible < 20.0
    assert visible == pytest.approx(cfg["slicing"]["memPerSliceMiB"] * 1024**2 / 1e9, rel=0.01)


def test_an_unsliced_config_sees_the_whole_card(cfg: dict[str, Any]) -> None:
    assert plan.visible_gb(cfg, 40.0) == 40.0


def test_slices_are_not_reported_as_gpus() -> None:
    """Two slices of one card counted as two GPUs overstates the hardware by exactly
    the factor that makes the cache arithmetic wrong."""
    cfg = plan.load(VALUES / "sliced.yaml", DEFAULTS)
    text = plan.text(cfg, "sliced", **A100)
    assert "slices of 1 GPU" in text
    assert "per slice" in text


def test_a_cache_too_small_to_hold_a_few_requests_is_flagged() -> None:
    """The other end of the same problem. A cache holding one or two requests evicts on
    nearly every arrival, so the run measures preemption thrash rather than admission —
    and a policy's refusals become indistinguishable from the engine's evictions.

    This is why sliced.yaml uses a 3B: two 7B models on one 40 GB card leave ~1 GB of
    cache between them.
    """
    cfg = plan.load(VALUES / "sliced.yaml", DEFAULTS)
    # Big enough that the cache is nearly gone, small enough to still pass the
    # 5%-headroom refusal — which is the gap this warning covers. A 7B here is refused
    # outright instead, which is also correct but tests the other check.
    cfg["model"]["params"] = 7.2e9
    text = plan.text(cfg, "sliced", **A100)
    assert "WARNING" in text
    assert "preempts" in text


def test_the_shipped_sliced_topology_does_not_trip_that_warning() -> None:
    cfg = plan.load(VALUES / "sliced.yaml", DEFAULTS)
    assert "WARNING" not in plan.text(cfg, "sliced", **A100)


# --------------------------------------------------------------------------
# Refusals that depend on the hardware, which the chart cannot know
# --------------------------------------------------------------------------


def test_fp8_on_an_a100_is_refused(cfg: dict[str, Any]) -> None:
    """fp8 tensor cores arrived with Hopper (sm90) and Ada (sm89). On an A100 (sm80) an
    fp8 checkpoint fails AFTER the weights download, which is the most expensive way to
    learn this."""
    cfg["model"]["quantization"] = "fp8"
    with pytest.raises(plan.PlanError, match="compute capability"):
        plan.text(cfg, "single", **A100)


def test_fp8_on_an_l40s_is_allowed(cfg: dict[str, Any]) -> None:
    """sm8.9. The refusal is about the card, not about fp8."""
    cfg["model"]["quantization"] = "fp8"
    assert plan.text(cfg, "single", hbm_gb=48.0, gpu_kind="L40S")


def test_weights_leaving_no_room_to_serve_are_refused(cfg: dict[str, Any]) -> None:
    """Fitting is not the bar. Fitting with room for a cache is."""
    cfg["model"]["params"] = 32e9  # 64 GB at bf16, against 36 GB usable
    with pytest.raises(plan.PlanError, match="fail to serve"):
        plan.text(cfg, "single", **A100)


def test_an_unknown_card_is_not_refused_on_a_guess(cfg: dict[str, Any]) -> None:
    """A card absent from the capability table is unknown, not disqualified. Refusing
    it would block hardware that works."""
    cfg["model"]["quantization"] = "fp8"
    assert plan.text(cfg, "single", hbm_gb=80.0, gpu_kind="B200")


# --------------------------------------------------------------------------
# The gap is stated, not hidden
# --------------------------------------------------------------------------


def test_the_plan_says_the_policy_is_not_wired(cfg: dict[str, Any]) -> None:
    """lib/gateway/admission.py and lib/router/router.py decide admission themselves
    and import nothing from admitperf, so the policy named in values reaches the pod's
    environment and is read by nothing.

    Stated in the plan because a silent gap means the next run measures the lab's
    admission logic while the report says the policy's name.
    """
    text = plan.text(cfg, "single", **A100)
    assert "NOT WIRED" in text
    assert "import nothing from admitperf" in text


def test_the_plan_names_the_policy_from_the_values(cfg: dict[str, Any]) -> None:
    cfg["gateway"]["admission"]["policy"] = "queue_depth"
    assert "queue_depth" in plan.text(cfg, "single", **A100)


# --------------------------------------------------------------------------
# The chart's own refusals, run here so CI covers them
# --------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_the_charts_refusals_all_fire() -> None:
    """infra/scripts/check-refusals.sh asserts nine refusals and eight guarantees
    against `helm template`. Running it from pytest means CI covers the combinations
    that used to deploy cleanly and report nothing: a KV transport with no handoff,
    slicing with no scheduler, a node with no device plugin.
    """
    if not (INFRA / "chart" / "charts").exists():
        pytest.skip("subcharts not fetched; run `make deps` in infra/")
    done = subprocess.run(
        ["bash", str(INFRA / "scripts" / "check-refusals.sh")],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "all checks passed" in done.stdout
