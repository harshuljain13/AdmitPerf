"""Whether a run on this cluster can mean anything. Read before renting hardware.

This is the one thing Helm cannot do and no deployment tool does: decide whether the
experiment is worth running. `helm template` tells you what will be deployed;
this tells you whether a KV-pressure policy could ever fire on it.

    make plan
    make plan VALUES=sliced

Section 3 is the point. From the values alone it gives the shortest request at which
KV fills before the scheduler cap. Below that the scheduler binds first, no threshold
is reachable at any arrival rate, and the run comes back flat for a reason no number
inside the run would reveal.

Everything else that used to live in render.py — manifest generation, host resolution,
port lists, package lists, component resolution — is Helm's job now.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

#: Fraction of HBM weights may occupy before there is no room left to serve. Fitting
#: is not the bar; fitting with room for a cache is.
MIN_KV_HEADROOM = 0.05

#: Compute capability by card, and what each generation can execute. fp8 tensor cores
#: arrived with Hopper (sm90) and Ada (sm89), so an fp8 checkpoint on an A100 fails
#: AFTER the weights download — the most expensive way to learn this.
CAPABILITY = {
    "H100": 9.0, "H200": 9.0, "GH200": 9.0,
    "L40S": 8.9, "L4": 8.9, "RTX4090": 8.9,
    "A100": 8.0, "A10": 8.6, "A6000": 8.6, "A40": 8.6,
    "V100": 7.0, "T4": 7.5,
}
MIN_CAPABILITY_FOR = {"fp8": 8.9}

BYTES_PER_PARAM = {"fp8": 1.0, "int8": 1.0, "awq": 0.5, "gptq": 0.5}
DEFAULT_BYTES_PER_PARAM = 2.0  # bf16 / fp16 / auto

#: Workloads the report is written against, so the table says which one can fire.
PROBES = ((8192, 512), (2048, 120))


class PlanError(ValueError):
    """The values cannot produce a useful experiment."""


def load(values: Path, defaults: Path) -> dict[str, Any]:
    """A values file merged over the chart's defaults, the way Helm merges them."""
    base = yaml.safe_load(defaults.read_text()) or {}
    over = yaml.safe_load(values.read_text()) or {}
    return _merge(base, over)


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def kv_bytes_per_token(cfg: dict[str, Any]) -> int | None:
    """2 (K and V) x layers x kv_heads x head_dim x dtype_bytes.

    Grouped-query attention keeps kv_heads well below the attention head count, which
    is why this is smaller than people expect: 56 KiB/token for a 7B here.
    """
    a = (cfg.get("model") or {}).get("attention")
    if not a:
        return None
    return 2 * int(a["layers"]) * int(a["kvHeads"]) * int(a["headDim"]) * int(a["dtypeBytes"])


def weights_gb(cfg: dict[str, Any]) -> float | None:
    params = (cfg.get("model") or {}).get("params")
    if not params:
        return None
    quant = (cfg.get("model") or {}).get("quantization")
    per = BYTES_PER_PARAM.get(str(quant), DEFAULT_BYTES_PER_PARAM)
    return float(params) * per / 1e9


def visible_gb(cfg: dict[str, Any], hbm_gb: float) -> float:
    """Memory one engine can actually see.

    A HAMi slice caps it well below the card. Computing from the full card instead
    overstated the cache by the slice factor — 30 GB of KV per slice on a 16 GiB
    slice — which is the same class of error as doing the KV arithmetic at
    max_model_len instead of at the workload's length.
    """
    if cfg.get("slicing", {}).get("enabled"):
        return float(cfg["slicing"]["memPerSliceMiB"]) * 1024**2 / 1e9
    return hbm_gb


def check(cfg: dict[str, Any], hbm_gb: float, gpu_kind: str) -> None:
    """Refusals that depend on the hardware, which the chart cannot know."""
    quant = (cfg.get("model") or {}).get("quantization")
    if quant and (need := MIN_CAPABILITY_FOR.get(str(quant))):
        have = CAPABILITY.get(gpu_kind)
        if have is not None and have < need:
            raise PlanError(
                f"{quant} needs compute capability {need}, and {gpu_kind} is {have}. "
                "This fails after the weights download, not at startup."
            )

    gb = weights_gb(cfg)
    if gb is None:
        return
    util = float(cfg["engine"]["gpuMemoryUtilization"])
    usable = visible_gb(cfg, hbm_gb) * util
    per_card = gb / max(1, int(cfg["pools"][0]["gpus"]))
    if per_card > usable * (1 - MIN_KV_HEADROOM):
        raise PlanError(
            f"weights are {per_card:.0f} GB of {usable:.0f} GB usable, leaving under "
            f"{MIN_KV_HEADROOM:.0%} for the cache. It would fit and then fail to serve."
        )


def text(cfg: dict[str, Any], name: str, *, hbm_gb: float, gpu_kind: str) -> str:
    check(cfg, hbm_gb, gpu_kind)

    eng = cfg["engine"]
    util = float(eng["gpuMemoryUtilization"])
    cap = int(eng["maxNumSeqs"])
    mml = int(eng["maxModelLen"])
    pools = cfg["pools"]
    engines = sum(int(p["replicas"]) for p in pools)
    gpus = sum(int(p["replicas"]) * int(p["gpus"]) for p in pools)
    # Slices share a card, so counting them as GPUs overstates the hardware by exactly
    # the factor that makes the cache arithmetic wrong.
    sliced = bool(cfg.get("slicing", {}).get("enabled"))
    hardware = f"{gpus} slices of 1 GPU" if sliced else f"{gpus} GPU"

    out = [
        f"{name}.yaml   {hardware} · {cfg['topology']['mode']} · {cfg['model']['id']}",
        "",
        "1  WHAT RUNS",
    ]
    for p in pools:
        for i in range(int(p["replicas"])):
            out.append(
                f"     vllm-{p['name']}-{i:<10} TP={p['tensorParallelSize']} "
                f"PP={p['pipelineParallelSize']}   {p['gpus']} {'slice' if sliced else 'GPU'}"
            )
    extras = [
        ("gateway", cfg["gateway"]["enabled"], "where an admission policy runs"),
        ("mooncake-store", cfg["kv"]["enabled"], f"KV transport ({cfg['kv']['backend']})"),
        ("prometheus + grafana", cfg["observability"]["enabled"], "11 dashboards"),
        ("dcgm-exporter", cfg["observability"]["enabled"] and cfg["observability"]["dcgm"]["enabled"], "per node"),
        ("open-webui", cfg["ui"]["enabled"], "chat, through the gateway"),
        ("hami", cfg["hami"]["enabled"], "GPU slicing"),
        ("keda", cfg["keda"]["enabled"], "autoscaling"),
    ]
    for label, on, note in extras:
        out.append(f"     {label:<22} {'yes' if on else '–':<4} {note}")

    # The fit, once. Engine settings are shared across pools by design.
    gb = weights_gb(cfg)
    pool_tokens = crossover = None
    if gb is not None and (per_token := kv_bytes_per_token(cfg)):
        per_gpu_weights = gb / int(pools[0]["gpus"])
        usable = visible_gb(cfg, hbm_gb) * util
        kv_gb = usable - per_gpu_weights
        pool_tokens = int(kv_gb * 1e9 / per_token)
        crossover = pool_tokens / cap
        out += [
            "",
            f"2  DOES IT FIT                                 per {'slice' if sliced else 'GPU'},"
            f" {visible_gb(cfg, hbm_gb):.0f}GB"
            + (f" slice of {gpu_kind} {hbm_gb:.0f}GB" if sliced else f" {gpu_kind}"),
            f"     weights     {per_gpu_weights:>5.0f} of {usable:.0f} GB usable   (util {util:g})",
            f"     KV cache    {kv_gb:>5.0f} GB  =  {pool_tokens / 1000:.0f}k tokens"
            f"   ({per_token / 1024:.0f} KiB/token)",
        ]

    if crossover is not None:
        out += [
            "",
            "3  CAN A POLICY FIRE                           the validity gate",
            f"     crossover   ~{crossover / 1000:.1f}k tokens/request"
            f"   ({pool_tokens / 1000:.0f}k pool / {cap} seqs)",
            "",
        ]
        # Which constraint binds is NOT a property of the values. The cache fills with
        # maxNumSeqs x tokensPerRequest and the request length comes from the
        # experiment, so this reports the crossover rather than naming a winner.
        for prompt, gen in PROBES:
            per = prompt + gen
            if per > crossover:
                out.append(
                    f"     {prompt:>5} + {gen:>3} = {per:>5} tok   KV binds"
                    "          a policy can fire"
                )
            else:
                out.append(
                    f"     {prompt:>5} + {gen:>3} = {per:>5} tok   scheduler binds"
                    f"   ceiling {cap * per / pool_tokens:.2f} — INERT"
                )
        out += [
            "",
            "     Below the crossover the scheduler cap fills before the cache, so",
            "     kv_used_fraction cannot reach a high threshold at ANY arrival rate.",
        ]
        # The other end of the same problem. A cache that holds one or two requests
        # evicts on nearly every arrival, so the run measures preemption thrash rather
        # than admission — and a policy's refusals are then indistinguishable from the
        # engine's evictions.
        longest = max(p + g for p, g in PROBES)
        concurrent = pool_tokens / longest
        if concurrent < 4:
            out += [
                "",
                f"     WARNING  the cache holds {concurrent:.1f} requests of {longest} tokens.",
                "     Under 4 means the engine preempts on nearly every arrival, and the",
                "     run measures eviction thrash rather than admission. Use a smaller",
                "     model, a shorter workload, or more memory.",
            ]

    # Stated, not hidden. The gateway carries these in its environment and nothing
    # reads them: lib/gateway/admission.py has its own tenant bucket and
    # lib/router/router.py its own KV_SATURATION check, and neither imports admitperf.
    adm = cfg["gateway"]["admission"]
    out += [
        "",
        "4  ADMISSION",
        f"     policy      {adm['policy']}  {adm.get('params') or {}}",
        "     NOT WIRED   lib/gateway and lib/router decide admission themselves and",
        "                 import nothing from admitperf. These values reach the pod's",
        "                 environment and are read by nothing.",
        "",
        f"   {engines} engine(s).  make up VALUES={name}",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print("usage: python -m plan values/single.yaml [--hbm 40] [--gpu A100]", file=sys.stderr)
        return 2
    values = Path(args[0])
    hbm = 40.0
    gpu = "A100"
    for i, a in enumerate(args):
        if a == "--hbm" and i + 1 < len(args):
            hbm = float(args[i + 1])
        if a == "--gpu" and i + 1 < len(args):
            gpu = args[i + 1]

    root = Path(__file__).resolve().parent
    try:
        cfg = load(values, root / "chart" / "values.yaml")
        print(text(cfg, values.stem, hbm_gb=hbm, gpu_kind=gpu))
    except PlanError as exc:
        print(f"this config cannot produce a useful experiment:\n  {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
