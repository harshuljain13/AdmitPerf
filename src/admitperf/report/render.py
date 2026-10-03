"""The AdmitPerf Report — one page, printed every run.

The survey's complaint is about what papers hand readers. A dashboard that only
exists while the cluster is up does not close that gap; a page that ships in the
results bundle does.

Two rules in the layout:

  1. The liveness verdict is the FIRST thing, before any outcome number. A reader
     who stops after one line should already know whether the run proves anything.
  2. Nothing is omitted for being unflattering. An unevidenced item says so.
"""

from __future__ import annotations

from dataclasses import dataclass

from admitperf.report.items import ItemStatus, RunFacts, evaluate
from admitperf.report.liveness import Verdict
from admitperf.report.taxonomy import AXIS_HELP

WIDTH = 68
RULE = "=" * WIDTH
THIN = "-" * WIDTH

#: Shown beside the verdict so the first line is self-explanatory without the
#: reader knowing this project's vocabulary.
VERDICT_GLOSS = {
    Verdict.LIVE: "signal reached the threshold; the policy had the opportunity to act",
    Verdict.MARGINAL: "signal approached but never reached the threshold; policy never fired",
    Verdict.INERT: "signal never approached the threshold; policy could not have fired",
    Verdict.UNKNOWN: "no signal recorded; whether the policy could have fired is unknown",
}


@dataclass(frozen=True)
class RunHeader:
    """Identity of the run. Every field here is what makes a number citable."""

    run_id: str
    cluster: str | None = None
    model: str | None = None
    engine: str | None = None
    commit: str | None = None


def _rows(left: list[str], right: list[str], gap: int = 2) -> list[str]:
    """Two columns, padded. The policy card and the signal range sit side by side
    so a reader compares the declared threshold against the observed range without
    scrolling between them."""
    width = max((len(x) for x in left), default=0)
    out = []
    for i in range(max(len(left), len(right))):
        lhs = left[i] if i < len(left) else ""
        rhs = right[i] if i < len(right) else ""
        out.append(f"{lhs.ljust(width)}{' ' * gap}|  {rhs}".rstrip())
    return out


def render(facts: RunFacts, header: RunHeader) -> str:
    """The page."""
    card, sig = facts.card, facts.signal
    items = evaluate(facts)
    lines: list[str] = [RULE, "AdmitPerf Report"]

    lines.append(f"run: {header.run_id}")
    for label, value in (
        ("cluster", header.cluster),
        ("model", header.model),
        ("engine", header.engine),
        ("commit", header.commit),
    ):
        if value:
            lines.append(f"{label}: {value}")
    lines.append(THIN)

    # Rule 1: the verdict leads.
    lines.append(f"SIGNAL LIVENESS: {sig.verdict.value}")
    lines.append(VERDICT_GLOSS[sig.verdict])
    lines.append("")

    left = [
        "POLICY",
        f"  name         {card.name}",
        f"  unit         {card.unit}",
        f"  setting      {card.setting}",
        f"  slo-aware    {card.slo_awareness}",
        f"  signal       {card.signal_quantity or '(none declared)'}  [{card.signal_structure}]",
        f"  metadata     {card.metadata_assumed}",
        f"  portability  Class {card.portability}",
    ]
    right = ["SIGNAL"]
    if sig.samples:
        thr = f"{sig.threshold:.4g}" if sig.threshold is not None else "(none)"
        right += [
            f"  threshold  {thr}",
            f"  min  {sig.min:.4g}      p50  {sig.p50:.4g}",
            f"  p95  {sig.p95:.4g}      p99  {sig.p99:.4g}",
            f"  max  {sig.max:.4g}",
            f"  crossed    {sig.crossings} of {sig.samples} decisions",
        ]
        if sig.missing:
            right.append(f"  MISSING    {sig.missing} decisions had no value")
    else:
        right.append("  no values recorded")
    lines += _rows(left, right)

    if sig.samples and sig.spark:
        lines += ["", f"  {sig.spark}"]
        if sig.threshold is not None:
            lines.append(f"  (full height = threshold {sig.threshold:.4g})")

    lines += ["", "DECISIONS"]
    lines.append(f"  {facts.decisions} total   {facts.rejects} rejected   {facts.defers} deferred")
    if facts.decisions and not (facts.rejects or facts.defers):
        lines.append("  nothing was refused, so no admission behaviour was exercised")

    lines += ["", "SEVEN REPORTING ITEMS"]
    for item in items:
        mark = {ItemStatus.OK: "ok  ", ItemStatus.FAIL: "FAIL", ItemStatus.UNKNOWN: "n/a "}[
            item.status
        ]
        lines.append(f"  {item.number} {item.title:<28} {mark}  {item.detail}")

    # Rule 2: anything a reader should not have to spot for themselves.
    notes = card.anomalies()
    if notes:
        lines += ["", "NOTES"]
        lines += [f"  - {n}" for n in notes]

    lines.append(RULE)
    return "\n".join(lines)


def render_markdown(facts: RunFacts, header: RunHeader) -> str:
    """The same content as Markdown, for pasting into a paper or an issue."""
    card, sig = facts.card, facts.signal
    items = evaluate(facts)
    out = [
        "# AdmitPerf Report",
        "",
        f"**Run:** `{header.run_id}`",
    ]
    for label, value in (
        ("Cluster", header.cluster),
        ("Model", header.model),
        ("Engine", header.engine),
        ("Commit", header.commit),
    ):
        if value:
            out.append(f"**{label}:** {value}  ")
    out += [
        "",
        f"## Signal liveness: {sig.verdict.value}",
        "",
        sig.explain() + ".",
        "",
        "## Policy card",
        "",
        "| Axis | Value | |",
        "| --- | --- | --- |",
    ]
    for axis, value in (
        ("Unit", card.unit),
        ("Setting", card.setting),
        ("Slo_awareness", card.slo_awareness),
        ("Signal_quantity", card.signal_quantity or "_none declared_"),
        ("Signal_structure", card.signal_structure),
        ("Metadata_assumed", card.metadata_assumed),
        ("Portability", f"Class {card.portability}"),
    ):
        out.append(f"| {axis} | `{value}` | {AXIS_HELP[axis.lower()]} |")

    if sig.samples:
        thr = f"`{sig.threshold:.4g}`" if sig.threshold is not None else "_none_"
        out += [
            "",
            "## Signal range",
            "",
            "| | |",
            "| --- | --- |",
            f"| Threshold | {thr} |",
            f"| min | `{sig.min:.4g}` |",
            f"| p50 | `{sig.p50:.4g}` |",
            f"| p95 | `{sig.p95:.4g}` |",
            f"| p99 | `{sig.p99:.4g}` |",
            f"| max | `{sig.max:.4g}` |",
            f"| Crossed threshold | {sig.crossings} of {sig.samples} decisions |",
        ]

    out += [
        "",
        "## Seven reporting items",
        "",
        "| | Item | | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        out.append(f"| {item.number} | {item.title} | {item.status.value} | {item.detail} |")

    notes = card.anomalies()
    if notes:
        out += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    return "\n".join(out)
