"""`report.json` — the artifact. Every other format renders from this.

The text page was the wrong thing to treat as the deliverable. A rendered page
cannot be compared across runs, cannot be asserted on in CI, and forces anything
that wants the numbers to re-derive them. Two consumers computing the same value
separately is how they come to disagree.

So the data is the artifact, it carries a schema version, and `render_*` functions
are pure functions of it. A renderer that computes anything is a bug.

    admitperf report decisions.jsonl --policy kv_threshold --format json -o report.json

    jq -e '.liveness.verdict != "INERT"' report.json   # fails a build
"""

from __future__ import annotations

import math
from typing import Any

from admitperf.report.items import ItemStatus, RunFacts, evaluate
from admitperf.report.liveness import SignalRange, Verdict
from admitperf.report.render import RunHeader
from admitperf.report.taxonomy import AXIS_HELP, PolicyCard

#: Bump the minor for an added field, the major for a removed or re-meaning one.
#: A report from today has to stay readable, or the cross-run comparison this
#: format exists to enable breaks the first time the schema moves.
SCHEMA_VERSION = "1.0"


def _num(value: float | None) -> float | None:
    """JSON has no NaN. An empty range must serialise as null, not as a number.

    `json.dumps` writes bare `NaN` by default, which is invalid JSON and which
    `jq` and every strict parser reject — so a run that recorded nothing would
    produce a file nothing could read.
    """
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return float(value)


def to_dict(facts: RunFacts, header: RunHeader) -> dict[str, Any]:
    """The whole report, as plain data."""
    sig, card = facts.signal, facts.card
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "id": header.run_id,
            "cluster": header.cluster,
            "model": header.model,
            "engine": header.engine,
            "commit": header.commit,
        },
        # First key after the run, because it is the first thing a reader needs
        # and the first thing a check should look at.
        "liveness": {
            "verdict": str(sig.verdict),
            "explanation": sig.explain(),
            # Negative once the threshold was crossed.
            "headroom_fraction": _num(sig.headroom),
        },
        "policy": {
            "name": card.name,
            "unit": card.unit,
            "setting": card.setting,
            "objective": card.objective,
            "signal": card.signal,
            "threshold": _num(card.threshold),
            "portability": card.portability,
            "requires": sorted(card.requires),
            "anomalies": card.anomalies(),
        },
        "signal": {
            "name": sig.name,
            "threshold": _num(sig.threshold),
            "samples": sig.samples,
            "missing": sig.missing,
            "min": _num(sig.min),
            "p50": _num(sig.p50),
            "p95": _num(sig.p95),
            "p99": _num(sig.p99),
            "max": _num(sig.max),
            "crossings": sig.crossings,
            "sparkline": sig.spark,
        },
        "decisions": {
            "total": facts.decisions,
            "rejected": facts.rejects,
            "deferred": facts.defers,
        },
        "reporting_items": [
            {
                "number": item.number,
                "title": item.title,
                "status": str(item.status),
                "detail": item.detail,
            }
            for item in evaluate(facts)
        ],
        "inputs": {
            "offered_rps": _num(facts.offered_rps),
            "capacity_rps": _num(facts.capacity_rps),
            "deadline_ms": _num(facts.deadline_ms),
            "unloaded_ttft_ms": _num(facts.unloaded_ttft_ms),
            "repeats": facts.repeats,
            "spread_p95_ms": _num(facts.spread_p95_ms),
            "params_source": facts.params_source,
            "metric_denominator": facts.metric_denominator,
            "config_sha": facts.config_sha,
        },
        "axis_help": AXIS_HELP,
    }


def from_dict(payload: dict[str, Any]) -> tuple[RunFacts, RunHeader]:
    """Rebuild `RunFacts` from a `report.json`, so renderers never re-derive.

    This is what lets the Streamlit dashboard, the HTML renderer and the text
    renderer share one source of truth: they all take the same object, and none of
    them recomputes a verdict.
    """
    got = str(payload.get("schema_version", "0"))
    if got.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"report.json is schema {got}, this build reads "
            f"{SCHEMA_VERSION}; major versions are not compatible"
        )

    s, p, run = payload["signal"], payload["policy"], payload["run"]

    def f(value: Any) -> float:
        return math.nan if value is None else float(value)

    sig = SignalRange(
        name=s["name"],
        threshold=s.get("threshold"),
        samples=int(s["samples"]),
        missing=int(s.get("missing", 0)),
        min=f(s.get("min")),
        p50=f(s.get("p50")),
        p95=f(s.get("p95")),
        p99=f(s.get("p99")),
        max=f(s.get("max")),
        crossings=int(s.get("crossings", 0)),
        spark=s.get("sparkline", ""),
    )
    card = PolicyCard(
        name=p["name"],
        unit=p["unit"],
        setting=p["setting"],
        objective=p["objective"],
        signal=p.get("signal"),
        threshold=p.get("threshold"),
        portability=p.get("portability", "A"),
        requires=frozenset(p.get("requires", ())),
    )
    d = payload.get("decisions", {})
    i = payload.get("inputs", {})
    facts = RunFacts(
        signal=sig,
        card=card,
        decisions=int(d.get("total", 0)),
        rejects=int(d.get("rejected", 0)),
        defers=int(d.get("deferred", 0)),
        offered_rps=i.get("offered_rps"),
        capacity_rps=i.get("capacity_rps"),
        deadline_ms=i.get("deadline_ms"),
        unloaded_ttft_ms=i.get("unloaded_ttft_ms"),
        repeats=int(i.get("repeats", 0)),
        spread_p95_ms=i.get("spread_p95_ms"),
        params_source=i.get("params_source"),
        metric_denominator=i.get("metric_denominator"),
        config_sha=i.get("config_sha"),
        commit=run.get("commit"),
    )
    header = RunHeader(
        run_id=run["id"],
        cluster=run.get("cluster"),
        model=run.get("model"),
        engine=run.get("engine"),
        commit=run.get("commit"),
    )
    return facts, header


# --------------------------------------------------------------------------
# Checks a build can run against the data
# --------------------------------------------------------------------------

#: Verdicts that mean the run cannot support a claim about the policy.
UNUSABLE = frozenset({str(Verdict.INERT), str(Verdict.UNKNOWN)})


def problems(payload: dict[str, Any]) -> list[str]:
    """Why this run should not be cited, if it should not be.

    Separate from rendering on purpose: a CI step wants the list, not a page. An
    empty list does not mean the policy worked — only that the run could show
    whether it did.
    """
    out: list[str] = []
    verdict = payload["liveness"]["verdict"]
    if verdict in UNUSABLE:
        out.append(f"liveness is {verdict}: {payload['liveness']['explanation']}")
    failed = [
        f"item {i['number']} ({i['title']})"
        for i in payload.get("reporting_items", ())
        if i["status"] == str(ItemStatus.FAIL)
    ]
    if failed:
        out.append("failed reporting items: " + ", ".join(failed))
    out.extend(payload["policy"].get("anomalies", ()))
    missing = payload["signal"].get("missing", 0)
    total = payload["decisions"].get("total", 0)
    if total and missing > total * 0.05:
        out.append(
            f"{missing} of {total} decisions recorded no signal value, so the range "
            "is computed from a partial run"
        )
    return out
