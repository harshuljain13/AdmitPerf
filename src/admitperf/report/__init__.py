"""The AdmitPerf Report.

The easy path is one call. Given a policy and the decisions a run produced, you
get the page:

    from admitperf.report import report_for

    print(report_for(policy, decisions, run_id="kv-090-agent-a"))

Nothing else is required of a policy author beyond declaring `signal` and
`threshold` on the policy class, because every other input is either read off the
policy or derived from the decision log the runner already writes.

`RunFacts` exists for the fuller version, where a caller supplies the things only
it knows — offered load, the measured ceiling, how many repeats there were. Those
are optional, and anything absent is reported as unevidenced rather than assumed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from admitperf.report.items import Item, ItemStatus, RunFacts, evaluate
from admitperf.report.liveness import (
    MARGINAL_BAND,
    SignalRange,
    Verdict,
    sparkline,
    summarise,
)
from admitperf.report.render import RunHeader, render, render_markdown
from admitperf.report.taxonomy import AXIS_HELP, VOCABULARY, PolicyCard

if TYPE_CHECKING:
    from admitperf.core.api import AdmissionPolicy


def facts_from(
    policy: AdmissionPolicy,
    decisions: Sequence[Any],
    **extra: Any,
) -> RunFacts:
    """Build `RunFacts` from a policy and its decision log.

    `decisions` is any sequence of objects carrying the signal value and a verdict
    — `DecisionRecord` is the obvious one, but a plain list of dicts works too, so
    a caller is never blocked on adopting our type.

    The signal value is looked up by the policy's own `signal` name first, and
    falls back to a `signal_value` field. That fallback is what lets a gateway log
    one column regardless of which policy is loaded.
    """
    name = policy.signal or "signal"

    def value_of(d: Any) -> float | None:
        for key in (name, "signal_value"):
            got = d.get(key) if isinstance(d, dict) else getattr(d, key, None)
            if isinstance(got, (int, float)):
                return float(got)
        return None

    def kind_of(d: Any) -> str:
        got = d.get("kind") if isinstance(d, dict) else getattr(d, "kind", "")
        return str(getattr(got, "value", got) or "").lower()

    kinds = [kind_of(d) for d in decisions]
    card = PolicyCard.of(policy)
    return RunFacts(
        signal=summarise([value_of(d) for d in decisions], name=name, threshold=card.threshold),
        card=card,
        decisions=len(decisions),
        rejects=sum(1 for k in kinds if k == "reject"),
        defers=sum(1 for k in kinds if k == "defer"),
        **extra,
    )


def report_for(
    policy: AdmissionPolicy,
    decisions: Sequence[Any],
    *,
    run_id: str,
    cluster: str | None = None,
    model: str | None = None,
    engine: str | None = None,
    commit: str | None = None,
    markdown: bool = False,
    **extra: Any,
) -> str:
    """One call, from a policy and its decisions to the finished page."""
    # commit identifies the run in the header AND evidences reporting item 7.
    # Passing it twice at the call site is the kind of duplication that gets one
    # of them wrong, so it is threaded here.
    if commit:
        extra.setdefault("commit", commit)
    facts = facts_from(policy, decisions, **extra)
    header = RunHeader(run_id=run_id, cluster=cluster, model=model, engine=engine, commit=commit)
    return (render_markdown if markdown else render)(facts, header)


__all__ = [
    "AXIS_HELP",
    "MARGINAL_BAND",
    "VOCABULARY",
    "Item",
    "ItemStatus",
    "PolicyCard",
    "RunFacts",
    "RunHeader",
    "SignalRange",
    "Verdict",
    "evaluate",
    "facts_from",
    "render",
    "render_markdown",
    "report_for",
    "sparkline",
    "summarise",
]
