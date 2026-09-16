"""Design an experiment: what to run on, what traffic, which policies.

The form mirrors the four separable problems the config has sections for —
infrastructure, traffic, policies, repeats — and validates through the same
`ExperimentConfig` the CLI uses. A setting that would fail at deploy time fails
here instead, in milliseconds rather than ten minutes into provisioning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from components import note, section, verdict, warn  # noqa: E402
from theme import HEADER, MUTED  # noqa: E402

from admitperf.core.config import ConfigError, ExperimentConfig  # noqa: E402
from admitperf.core.registry import available  # noqa: E402

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments"

#: Parameters worth exposing per policy, with a plain description. Anything not
#: listed still works from a YAML file; this is the common set, not the limit.
POLICY_PARAMS: dict[str, list[tuple[str, str, float, float, float, str]]] = {
    "queue_depth": [("max_waiting", "Refuse when more than N are queued", 0, 128, 8, "%d")],
    "queue_depth_defer": [("max_waiting", "Hold when more than N are queued", 0, 128, 8, "%d")],
    "kv_threshold": [("threshold", "Refuse above this KV cache fraction", 0.05, 1.0, 0.9, "%.2f")],
    "chronos_inspired": [
        ("chunk_tokens", "Prefill chunk size the cost model assumes", 64, 4096, 512, "%d"),
        (
            "safety_factor",
            "Margin on the predicted wait — higher sheds sooner",
            0.5,
            5.0,
            1.0,
            "%.1f",
        ),
    ],
}


def render() -> None:
    st.markdown(HEADER, unsafe_allow_html=True)
    st.markdown("## Design an experiment")
    note(
        "Every policy will face the <b>same</b> deployment and the <b>same</b> "
        "seeded traffic, so the only thing that differs between them is the "
        "accept-or-refuse decision. That is what makes the comparison mean "
        "something."
    )

    name = st.text_input("Name", "my-experiment", help="Used for the results directory.")

    tab_infra, tab_traffic, tab_policies, tab_yaml = st.tabs(
        ["1 · Machine", "2 · Traffic", "3 · Policies", "4 · Review"]
    )

    # --- infrastructure ---------------------------------------------------

    with tab_infra:
        section("What to run on", "The engine every policy will be measured against.")
        c1, c2, c3 = st.columns(3)
        model = c1.text_input("Model", "Qwen/Qwen2.5-0.5B-Instruct")
        gpu = c2.selectbox("GPU", ["A10G", "A100", "H100", "L4", "T4"], index=0)
        gpu_count = c3.number_input("How many GPUs", 1, 8, 1)

        section(
            "Capacity",
            "The limit that creates queueing. Without a binding limit nothing "
            "queues, and every policy scores the same because there is no "
            "pressure to manage.",
        )
        c1, c2, c3 = st.columns(3)
        max_num_seqs = c1.number_input(
            "Concurrent requests", 1, 256, 4, help="The engine's own cap. Small on purpose."
        )
        max_model_len = c2.number_input("Context length", 512, 131072, 2048, step=512)
        gpu_mem = c3.slider("GPU memory to use", 0.1, 0.99, 0.55)

        with st.expander("Advanced engine settings"):
            c1, c2 = st.columns(2)
            tp = c1.number_input("Tensor parallel", 1, 8, 1)
            pp = c2.number_input("Pipeline parallel", 1, 8, 1)
            prefix_cache = st.checkbox(
                "Enable prefix caching",
                value=False,
                help="Off for benchmarking. With it on, repeated prompts skip "
                "prefill and pressure stops tracking offered load — the signal "
                "most policies read goes dead.",
            )
            sched = st.selectbox("Engine scheduling", ["fcfs", "priority"], index=0)
            concurrent_inputs = st.number_input(
                "Platform concurrency",
                1,
                4096,
                256,
                help="Requests the container accepts at once. Must exceed the "
                "engine cap above, or traffic queues in front of the engine and "
                "the benchmark measures the platform instead.",
            )

    # --- traffic ----------------------------------------------------------

    with tab_traffic:
        section("How much traffic", "Arrivals are random and bursty, the way real traffic is.")
        c1, c2 = st.columns(2)
        rate = c1.number_input(
            "Arrivals per second",
            1.0,
            500.0,
            15.0,
            help="Push this above what the engine can drain.",
        )
        seed = c2.number_input("Seed", 0, 10_000, 0, help="Same seed, same traffic.")

        mode = st.radio(
            "Run length",
            ["Fixed duration", "Fixed request count"],
            horizontal=True,
            help="Duration is preferred for comparisons: with a fixed count a "
            "policy that refuses most traffic finishes early and is measured "
            "over a shorter, quieter window than the baseline.",
        )
        duration_s = warmup_s = None
        n = 200
        if mode == "Fixed duration":
            c1, c2 = st.columns(2)
            duration_s = c1.number_input("Seconds", 10, 3600, 120)
            warmup_s = c2.number_input(
                "Warmup seconds",
                0,
                600,
                20,
                help="Sent but excluded from results — they pay for cold caches, "
                "which is not the policy's doing.",
            )
        else:
            n = st.number_input("Requests", 10, 100_000, 200)

        slo_mode = st.radio(
            "Deadlines",
            ["absolute", "relative"],
            horizontal=True,
            help="Relative expresses deadlines as multiples of the engine's "
            "unloaded latency, so they mean the same thing on a small model and "
            "a large one. Needs `admitperf infra calibrate` first.",
        )

    # --- policies ---------------------------------------------------------

    with tab_policies:
        section("Which policies to compare", "Include a baseline, or there is nothing to beat.")
        registry = sorted(available())
        chosen = st.multiselect(
            "Policies",
            registry,
            default=[p for p in ("no_admission", "queue_depth") if p in registry],
        )

        policies: list[dict | str] = []
        for policy in chosen:
            params = POLICY_PARAMS.get(policy)
            if not params:
                policies.append(policy)
                continue
            with st.expander(f"⚙  {policy}", expanded=len(chosen) <= 3):
                entry: dict = {"name": policy}
                for key, label, lo, hi, default, fmt in params:
                    if fmt == "%d":
                        entry[key] = int(
                            st.number_input(
                                label, int(lo), int(hi), int(default), key=f"{policy}-{key}"
                            )
                        )
                    else:
                        entry[key] = float(
                            st.slider(
                                label, float(lo), float(hi), float(default), key=f"{policy}-{key}"
                            )
                        )
                policies.append(entry)

        repeats = st.number_input(
            "Repeats",
            1,
            10,
            3,
            help="A live engine gives a different number every run. One sample "
            "per policy is a measurement, not a comparison.",
        )
        if repeats < 2:
            warn(
                "With a single repeat there is no spread, so a small difference "
                "between policies cannot be told apart from noise."
            )

    # --- review -----------------------------------------------------------

    config: dict = {
        "name": name or "unnamed",
        "infra": {
            "provider": "modal",
            "gpu": gpu,
            "gpu_count": int(gpu_count),
            "model": model,
            "max_concurrent_inputs": int(concurrent_inputs),
            "engine": {
                "max_num_seqs": int(max_num_seqs),
                "max_model_len": int(max_model_len),
                "gpu_memory_utilization": float(gpu_mem),
                "tensor_parallel_size": int(tp),
                "pipeline_parallel_size": int(pp),
                "enable_prefix_caching": bool(prefix_cache),
                "scheduling_policy": sched,
            },
        },
        "workload": {
            "kind": "poisson",
            "rate": float(rate),
            "seed": int(seed),
            "slo_mode": slo_mode,
            **(
                {"duration_s": int(duration_s), "warmup_s": int(warmup_s)}
                if duration_s
                else {"n": int(n)}
            ),
        },
        "policies": policies or ["no_admission"],
        "bench": {"repeats": int(repeats)},
    }

    with tab_yaml:
        section("Review", "Validated with exactly the checks the CLI runs.")

        try:
            parsed = ExperimentConfig.from_dict(config)
        except ConfigError as exc:
            warn(f"<b>This configuration will not run.</b><br>{exc}")
            parsed = None

        if parsed:
            total = len(parsed.policies) * parsed.bench.repeats
            verdict(
                f"{len(parsed.policies)} policies × {parsed.bench.repeats} repeats = {total} runs",
                f"Each faces the same {parsed.infra.model} on {parsed.infra.modal_gpu}, "
                f"capped at {parsed.infra.engine.max_num_seqs} concurrent requests, "
                f"with identical seeded traffic at {parsed.workload.rate}/s.",
            )

        text = yaml.safe_dump(config, sort_keys=False)
        st.code(text, language="yaml")

        c1, c2 = st.columns([1, 3])
        filename = c2.text_input(
            "Save as", f"{name or 'experiment'}.yaml", label_visibility="collapsed"
        )
        if c1.button("Save to experiments/", disabled=parsed is None, width="stretch"):
            EXPERIMENTS.mkdir(exist_ok=True)
            path = EXPERIMENTS / filename
            path.write_text(text)
            st.session_state["last_config"] = str(path)
            st.success(
                f"Saved `{path.relative_to(EXPERIMENTS.parent)}` — open the **Run** page next."
            )

        st.markdown(
            f'<p style="color:{MUTED};font-size:0.8rem;margin-top:1rem">'
            "Anything not offered here still works by editing the YAML — the form "
            "covers the common settings, not every one.</p>",
            unsafe_allow_html=True,
        )


render()
