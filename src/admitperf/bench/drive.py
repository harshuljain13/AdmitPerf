"""Run one experiment: read the config, drive load, write the report.

    admitperf run experiments/signal-liveness --mock

Deliberately small. Every step is visible in one read — arrive, scrape, decide,
record — because a measurement is only as trustworthy as the instrument, and an
instrument nobody can audit is not evidence.

What it reads
-------------
The experiment config says what load to send. The CLUSTER config it points at
says everything else, including which admission policy is active:

    experiments/signal-liveness/experiment.yaml
        cluster: infra/config/single.yaml
        load: {n, rate, seed, prompt_tokens, output_tokens}
        baseline: no_admission
        repeats: 3

    infra/config/single.yaml
        admission: {policy: kv_threshold, params: {threshold: 0.90}}

What it writes
--------------
One directory per arm per repeat, under the experiment's own `results/`:

    results/kv_threshold-r1/{decisions.jsonl,report.json}
    results/no_admission-r1/{decisions.jsonl,report.json}

`report.json` is the artifact. The dashboard and every renderer read it and
compute nothing.
"""

from __future__ import annotations

import contextlib
import json
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from admitperf.core.api import DecisionKind, Request, SystemState
from admitperf.core.registry import get_policy
from admitperf.report import RunHeader, facts_from, to_dict


class ExperimentError(RuntimeError):
    """The experiment cannot run. Raised before any load is sent."""


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Experiment:
    """An experiment, with its cluster config already resolved."""

    name: str
    path: Path
    cluster_path: Path
    cluster: dict[str, Any]
    load: dict[str, Any]
    baseline: str | None
    repeats: int
    expect: dict[str, Any]

    @property
    def policy_name(self) -> str:
        """The policy under test, from the CLUSTER config.

        Not from the experiment. A deployment runs one admission policy, and
        comparing policies means comparing deployments that differ in nothing
        else — so the policy is a property of what is deployed.
        """
        return str((self.cluster.get("admission") or {}).get("policy") or "")

    @property
    def policy_params(self) -> dict[str, Any]:
        return dict((self.cluster.get("admission") or {}).get("params") or {})


def load_experiment(path: str | Path) -> Experiment:
    """Read an experiment and the cluster config it points at."""
    p = Path(path)
    if p.is_dir():
        p = p / "experiment.yaml"
    if not p.is_file():
        raise ExperimentError(f"no experiment config at {p}")

    raw = yaml.safe_load(p.read_text()) or {}
    cluster_ref = raw.get("cluster")
    if not cluster_ref:
        raise ExperimentError(
            f"{p} declares no `cluster:`. An experiment says what load to send; the "
            "cluster config says what is deployed and which policy is active. "
            "Without the pointer there is nothing to send load at."
        )

    # Relative to the repo root, so the same string works from any directory.
    from admitperf.reports.paths import repo_root

    cluster_path = Path(cluster_ref)
    if not cluster_path.is_absolute():
        cluster_path = repo_root() / cluster_path
    if not cluster_path.is_file():
        raise ExperimentError(f"{p} points at {cluster_ref}, which does not exist")

    from infra.render import load as load_cluster

    cluster = load_cluster(cluster_path)

    exp = Experiment(
        name=str(raw.get("name") or p.parent.name),
        path=p,
        cluster_path=cluster_path,
        cluster=cluster,
        load=dict(raw.get("load") or {}),
        baseline=raw.get("baseline"),
        repeats=int(raw.get("repeats", 1)),
        expect=dict(raw.get("expect") or {}),
    )
    if not exp.policy_name:
        raise ExperimentError(
            f"{cluster_ref} declares no admission.policy, so there is nothing to "
            "measure. Add one, or point at a cluster config that has one."
        )
    return exp


# --------------------------------------------------------------------------
# The engine, over HTTP
# --------------------------------------------------------------------------


@dataclass
class Scrape:
    kv_used_fraction: float | None
    running: int
    waiting: int
    ok: bool
    error: str | None = None


_GAUGES = {
    "kv": "vllm:kv_cache_usage_perc",
    "running": "vllm:num_requests_running",
    "waiting": "vllm:num_requests_waiting",
}


def scrape(url: str, timeout: float = 2.0) -> Scrape:
    """Read /metrics and pull out the gauges a policy can decide on.

    A failed scrape is recorded as a failure, never as zeros. Zeros read as a
    completely idle engine, which is the most dangerous possible wrong answer: the
    policy sees headroom, admits everything, and scores identically to no policy
    at all.
    """
    try:
        with urllib.request.urlopen(f"{url}/metrics", timeout=timeout) as r:
            text = r.read().decode()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Scrape(None, 0, 0, ok=False, error=str(exc))

    found: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        for key, metric in _GAUGES.items():
            if line.startswith(metric):
                with contextlib.suppress(IndexError, ValueError):
                    found[key] = float(line.rsplit(" ", 1)[1])
    if "kv" not in found:
        return Scrape(None, 0, 0, ok=False, error="kv gauge absent from /metrics")
    return Scrape(
        kv_used_fraction=found["kv"],
        running=int(found.get("running", 0)),
        waiting=int(found.get("waiting", 0)),
        ok=True,
    )


def _complete(url: str, model: str, prompt_tokens: int, output_tokens: int) -> None:
    """One completion. Errors are swallowed: this is load, not a correctness test."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "x " * prompt_tokens}],
            "max_tokens": output_tokens,
        }
    ).encode()
    req = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    # A dropped request is load, not a failure: the point here is to occupy the
    # engine, and whether this particular completion returned is not the
    # measurement. Scrape failures ARE recorded, because those corrupt the signal.
    with contextlib.suppress(Exception), urllib.request.urlopen(req, timeout=120) as r:
        r.read()


# --------------------------------------------------------------------------
# One arm
# --------------------------------------------------------------------------


@dataclass
class ArmResult:
    decisions: list[dict[str, Any]]
    scrapes_ok: int
    scrapes_failed: int
    max_lag_ms: float
    offered_rps: float
    wall_s: float


def run_arm(
    exp: Experiment,
    *,
    policy_name: str,
    engine_url: str,
    seed: int,
) -> ArmResult:
    """Drive the experiment's load through one policy.

    Arrivals are scheduled against an ABSOLUTE clock, not cumulative sleeps. A
    loop that sleeps 1/rate between requests drifts slower than the stated rate by
    whatever each iteration costs, and then every load figure is quietly wrong. The
    worst lag is recorded so drift is visible rather than assumed away.
    """
    params = exp.policy_params if policy_name == exp.policy_name else {}
    policy = get_policy(policy_name, **params)

    n = int(exp.load.get("n", 100))
    rate = float(exp.load.get("rate", 10))
    prompt_tokens = int(exp.load.get("prompt_tokens", 512))
    output_tokens = int(exp.load.get("output_tokens", 64))
    kind = str(exp.load.get("kind", "poisson"))
    model = str(exp.cluster.get("model", {}).get("id") or "lab")

    rng = random.Random(seed)
    # Poisson arrivals: exponential gaps. Precomputed from a seeded generator, so
    # every arm faces the SAME trace — otherwise a difference between policies may
    # be a difference between traces.
    gaps = [rng.expovariate(rate) for _ in range(n)] if kind == "poisson" else [1.0 / rate] * n
    offsets: list[float] = []
    acc = 0.0
    for g in gaps:
        offsets.append(acc)
        acc += g

    decisions: list[dict[str, Any]] = []
    threads: list[threading.Thread] = []
    ok = failed = 0
    max_lag_ms = 0.0

    started = time.perf_counter()
    for i, offset in enumerate(offsets):
        due = started + offset
        now = time.perf_counter()
        if due > now:
            time.sleep(due - now)
        lag_ms = max(0.0, (time.perf_counter() - due) * 1000)
        max_lag_ms = max(max_lag_ms, lag_ms)

        s = scrape(engine_url)
        ok += s.ok
        failed += not s.ok

        state = SystemState(
            now=time.time(),
            kv_used_fraction=s.kv_used_fraction,
            running_requests=s.running,
            waiting_requests=s.waiting,
            running_agents=0,
            per_tenant_running={},
            per_tenant_admitted_recent={},
            engine_metrics={},
        )
        req = Request(
            request_id=f"r{i}",
            tenant_id="t0",
            arrival_time=offset,
            input_tokens=prompt_tokens,
        )
        verdict = policy.decide(req, state)

        decisions.append(
            {
                "request_id": req.request_id,
                "arrival_s": round(offset, 4),
                "kind": verdict.kind.value,
                "reason": verdict.reason,
                # Read THROUGH the policy, so a derived signal is captured too and
                # the log holds the number the decision was actually made on.
                "signal_value": policy.read_signal(state),
                "kv_used_fraction": s.kv_used_fraction,
                "waiting_requests": s.waiting,
                "running_requests": s.running,
                "scrape_ok": s.ok,
                "lag_ms": round(lag_ms, 2),
            }
        )

        if verdict.kind is DecisionKind.ADMIT:
            t = threading.Thread(
                target=_complete,
                args=(engine_url, model, prompt_tokens, output_tokens),
                daemon=True,
            )
            t.start()
            threads.append(t)

    for t in threads:
        t.join(timeout=120)
    wall = time.perf_counter() - started

    return ArmResult(
        decisions=decisions,
        scrapes_ok=ok,
        scrapes_failed=failed,
        max_lag_ms=max_lag_ms,
        offered_rps=n / max(1e-9, wall),
        wall_s=wall,
    )


# --------------------------------------------------------------------------
# The whole experiment
# --------------------------------------------------------------------------


def _hardware(cluster: dict[str, Any]) -> str:
    """Cards and memory, from the cluster config's own host list."""
    hosts = cluster.get("hosts") or []
    if not isinstance(hosts, list) or not hosts:
        return "unknown"
    bits = []
    for h in hosts:
        g = (h or {}).get("gpu") or {}
        bits.append(f"{g.get('count', '?')}x{g.get('kind', '?')}-{g.get('hbm_gb', '?')}GB")
    topo = cluster.get("topology") or {}
    pools = topo.get("pools") or {}
    shape = " ".join(f"{k}x{(v or {}).get('replicas', 1)}" for k, v in pools.items())
    return f"{', '.join(bits)} · {topo.get('mode', '?')} {shape}".strip()


def write_arm(
    exp: Experiment,
    arm: ArmResult,
    *,
    policy_name: str,
    repeat: int,
    engine: str,
    out_root: Path,
) -> Path:
    """One directory per arm per repeat: the decisions, and the report."""
    out = out_root / f"{policy_name}-r{repeat}"
    out.mkdir(parents=True, exist_ok=True)

    with (out / "decisions.jsonl").open("w") as f:
        for d in arm.decisions:
            f.write(json.dumps(d) + "\n")

    params = exp.policy_params if policy_name == exp.policy_name else {}
    facts = facts_from(
        get_policy(policy_name, **params),
        arm.decisions,
        offered_rps=round(arm.offered_rps, 3),
        repeats=exp.repeats,
        params_source="cluster config",
        # admitted_within_slo is deliberately NOT passed: this driver records no
        # per-request latency yet, so it cannot say which admitted requests met an
        # SLO. Reporting item 6 therefore comes back unevidenced, which is correct —
        # naming a denominator without a figure satisfies nothing.
        config_sha=exp.cluster_path.name,
        # Reporting item 7, field by field from the config that produced the run. The
        # survey asks for the identity of the baseline ACTUALLY run, by name, because
        # a paper naming one and running another is undetectable from its results.
        disclosure={
            "model": exp.cluster.get("model", {}).get("id"),
            "engine": engine,
            "hardware": _hardware(exp.cluster),
            "workload": (
                f"{exp.load.get('kind')} n={exp.load.get('n')} "
                f"rate={exp.load.get('rate')} seed={exp.load.get('seed')} "
                f"prompt={exp.load.get('prompt_tokens')} "
                f"out={exp.load.get('output_tokens')}"
            ),
            "offered_load": f"{arm.offered_rps:.3g} rps",
            "baseline": exp.baseline or "none",
        },
    )
    header = RunHeader(
        run_id=f"{exp.name}-{policy_name}-r{repeat}",
        cluster=str(exp.cluster_path.name),
        model=str(exp.cluster.get("model", {}).get("id") or "unknown"),
        engine=engine,
    )
    payload = to_dict(facts, header)
    # Harness health, recorded beside the result. A run where scrapes failed
    # produces ordinary-looking numbers that mean nothing, because the policy
    # decided on a stale snapshot — a different failure from an inert signal, and
    # invisible unless it is written down.
    payload["harness"] = {
        "scrapes_ok": arm.scrapes_ok,
        "scrapes_failed": arm.scrapes_failed,
        "max_arrival_lag_ms": round(arm.max_lag_ms, 2),
        "wall_s": round(arm.wall_s, 2),
    }
    payload["expect"] = exp.expect

    # Both configs, verbatim, inside the report. This is what makes the three views
    # connect: a reader opens one file and sees the result, the load that produced
    # it, and the deployment it ran against. Referencing them by path instead would
    # let an edit after the run change what the result appears to have measured.
    payload["configs"] = {
        "experiment": {
            "path": str(exp.path),
            "yaml": exp.path.read_text(),
        },
        "cluster": {
            "path": str(exp.cluster_path),
            "yaml": exp.cluster_path.read_text(),
            # Resolved, because `extends` means the file on disk is not the whole
            # story and the resolved form is what actually ran.
            "resolved": exp.cluster,
        },
    }
    (out / "report.json").write_text(json.dumps(payload, indent=2) + "\n")

    # Written here rather than on demand, so the page and the data are produced
    # together and a stale HTML cannot outlive the json it came from.
    from admitperf.report import render_html

    (out / "report.html").write_text(render_html(payload) + "\n")
    return out
