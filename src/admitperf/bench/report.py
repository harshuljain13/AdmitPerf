"""Turn a results directory into a report someone can read or attach.

`bench compare` prints a table, which answers "what were the numbers" for
whoever is still sitting at the terminal. A report has to answer a different
question — *which policy, by how much, and can I trust it* — for a reader who
was not there, possibly months later, possibly a reviewer.

Three decisions worth stating:

**One file.** Figures are embedded as base64 PNGs, so the report opens with no
server, no CDN and no sibling directory. A report that only renders next to its
assets stops being readable the first time it is emailed.

**Light, not the app's dark theme.** This is the artifact that gets printed and
pasted into a paper. Charts are also written to `figures/` as PNGs for exactly
that.

**The caveats outrank the result.** Degraded runs, single repeats and gaps
inside the run-to-run noise are stated above the table, not in a footnote,
because a reader who stops after the headline should stop with the right
impression.
"""

from __future__ import annotations

import base64
import html
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from admitperf.bench.compare import PolicyStats, compare_dir

# The brand, restated here rather than imported: `src/` must not depend on the
# dashboard. `tests/test_report.py` asserts the two stay in step.
INK = "#0B0B0B"
YELLOW = "#F5C518"
GREY = "#BFBFBF"
MUTED = "#8A8A8A"
RED = "#FF5A52"
FONT = "Helvetica, Arial, sans-serif"

#: The reference every other policy is read against, when it was run.
BASELINE = "no_admission"


@dataclass
class Bundle:
    path: Path
    manifest: dict[str, Any]
    summary: dict[str, Any]

    @property
    def deployment(self) -> str | None:
        return self.manifest.get("deployment")

    @property
    def label(self) -> str:
        return self.manifest.get("policy_label") or self.manifest.get("policy") or "unknown"


@dataclass
class Figure:
    name: str
    title: str
    caption: str
    png: bytes


@dataclass
class Section:
    """One deployment. Never more — policies are comparable only when they
    faced the same engine on the same hardware."""

    deployment: str
    headline: str
    detail: str
    tone: str  # "good" | "flat" | "warn"
    caveats: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


@dataclass
class Report:
    experiment: str
    generated_at: str
    sections: list[Section]
    table: str
    provenance: dict[str, Any]
    unavailable: dict[str, str]
    notes: list[str] = field(default_factory=list)


def load_bundles(root: Path) -> list[Bundle]:
    """Every readable run under a directory tree.

    A half-written bundle from an interrupted run is skipped rather than
    raised on: results accumulate over months, and one bad directory should not
    cost you the report for the other twenty.
    """
    bundles: list[Bundle] = []
    for summary_path in sorted(root.rglob("summary.json")):
        manifest_path = summary_path.parent / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            summary = json.loads(summary_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        bundles.append(Bundle(summary_path.parent, manifest, summary))
    return bundles


def _stats(bundles: list[Bundle]) -> dict[str, PolicyStats]:
    stats: dict[str, PolicyStats] = {}
    for b in bundles:
        stats.setdefault(b.label, PolicyStats(b.label)).add(b.summary)
    return stats


def _median(values: list[float]) -> float | None:
    import statistics

    return statistics.median(values) if values else None


def _half_range(values: list[float]) -> float:
    """Half the observed min-max range. Not a confidence interval — with three
    repeats nothing deserves that name — but enough to say whether a gap is
    bigger than the noise around it."""
    return (max(values) - min(values)) / 2 if len(values) > 1 else 0.0


def _verdict(stats: dict[str, PolicyStats]) -> tuple[str, str, str]:
    """Which policy, by how much, and whether the gap survives the spread."""
    scored = [(s, _median(s.offered)) for s in stats.values()]
    scored = [(s, m) for s, m in scored if m is not None]
    if not scored:
        return (
            "No attainment was recorded",
            "Every run is missing <code>offered_attainment</code>. Usually the "
            "engine never answered, so there is nothing to compare.",
            "warn",
        )
    if len(scored) == 1:
        s, m = scored[0]
        return (
            f"{html.escape(s.label)} met {m:.1%} of what arrived",
            "Only one policy was run, so this is a measurement, not a "
            "comparison. Add a second policy — <code>no_admission</code> is the "
            "one worth having — before reading anything into it.",
            "flat",
        )

    scored.sort(key=lambda pair: pair[1], reverse=True)
    best, best_score = scored[0]
    reference, ref_score = next(
        ((s, m) for s, m in scored[1:] if s.label.startswith(BASELINE)),
        scored[1],
    )
    gap = (best_score - ref_score) * 100
    noise = max(_half_range(best.offered), _half_range(reference.offered)) * 100

    if best.label == reference.label or gap <= noise:
        return (
            "No policy is distinguishable from the others",
            f"The best gap is {gap:.1f} points, inside the ±{noise:.1f} points "
            "of run-to-run spread. More repeats, or a load high enough to make "
            "admission matter, before claiming a winner.",
            "flat",
        )
    return (
        f"{html.escape(best.label)} beats {html.escape(reference.label)} "
        f"by {gap:.1f} points of offered attainment",
        f"{best_score:.1%} of arriving requests met their promise, against "
        f"{ref_score:.1%}. The gap is larger than the ±{noise:.1f} points of "
        "spread across repeats, so it is not obviously noise.",
        "good",
    )


def _caveats(stats: dict[str, PolicyStats]) -> list[str]:
    # Caveats carry their own markup, so anything coming from a config — a
    # policy label is user-supplied — is escaped as it goes in.
    out = []
    degraded = sorted(html.escape(s.label) for s in stats.values() if s.unhealthy)
    if degraded:
        out.append(
            f"<b>{', '.join(degraded)} had runs where most metric scrapes failed.</b> "
            "The policy was deciding on a stale snapshot, so those numbers "
            "describe the workload, not the policy."
        )
    single = sorted(html.escape(s.label) for s in stats.values() if s.runs == 1)
    if single:
        out.append(
            f"<b>Only one run for {', '.join(single)}.</b> There is no spread to "
            "report, so no way to tell a real difference from a noisy one. "
            "Three repeats is the minimum worth defending."
        )
    flat = sorted(
        html.escape(s.label)
        for s in stats.values()
        if s.rejects and set(s.rejects) == {"no_signal"}
    )
    if flat:
        out.append(
            f"<b>{', '.join(flat)} only ever refused for <code>no_signal</code>.</b> "
            "It never read the state it is supposed to decide on, which makes "
            "it accept-everything wearing a policy's name."
        )
    return out


# --- figures ---------------------------------------------------------------


def _figures(stats: dict[str, PolicyStats]) -> tuple[list[Figure], list[str]]:
    """Charts as PNG bytes, or a note explaining their absence.

    matplotlib lives in the `analysis` extra, so a minimal install can still
    produce the report — with the tables, and a line saying why there are no
    pictures, rather than a traceback.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return [], [
            "Figures were skipped: matplotlib is not installed. "
            "<code>pip install -e '.[analysis]'</code> and run the report again."
        ]

    labels = sorted(stats)
    figures: list[Figure] = []

    def _fig(name: str, title: str, caption: str, draw) -> None:
        fig, ax = plt.subplots(figsize=(7.2, 0.6 * len(labels) + 2.2), dpi=160)
        draw(ax)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        import io

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        plt.close(fig)
        figures.append(Figure(name, title, caption, buf.getvalue()))

    def attainment(ax) -> None:
        offered = [_median(stats[x].offered) or 0 for x in labels]
        served = [_median(stats[x].served) or 0 for x in labels]
        err = [_half_range(stats[x].offered) for x in labels]
        y = range(len(labels))
        ax.barh([i + 0.2 for i in y], offered, 0.36, xerr=err, color=YELLOW, label="of arrived")
        ax.barh([i - 0.2 for i in y], served, 0.36, color=GREY, label="of admitted")
        ax.set_yticks(list(y), labels)
        ax.set_xlabel("SLO attainment")
        ax.set_xlim(0, 1)
        ax.legend(frameon=False, fontsize=8)

    def ttft(ax) -> None:
        values = [_median(stats[x].ttft_p95) or 0 for x in labels]
        err = [_half_range(stats[x].ttft_p95) for x in labels]
        ax.barh(list(range(len(labels))), values, 0.5, xerr=err, color=INK)
        ax.set_yticks(list(range(len(labels))), labels)
        ax.set_xlabel("TTFT p95 (ms), served requests only")

    def rejects(ax) -> None:
        reasons = sorted({r for s in stats.values() for r in s.rejects})
        left = [0.0] * len(labels)
        shades = [YELLOW, RED, MUTED, GREY, INK]
        for i, reason in enumerate(reasons):
            widths = [float(stats[x].rejects.get(reason, 0)) for x in labels]
            ax.barh(
                list(range(len(labels))),
                widths,
                0.5,
                left=left,
                color=shades[i % len(shades)],
                label=reason,
            )
            left = [a + b for a, b in zip(left, widths, strict=True)]
        ax.set_yticks(list(range(len(labels))), labels)
        ax.set_xlabel("requests refused, by reason")
        ax.legend(frameon=False, fontsize=8)

    _fig(
        "attainment",
        "What each policy delivered",
        "Of arrived is the headline: refusals count as misses, so a policy "
        "cannot improve it by refusing more — only by refusing better. Of "
        "admitted is shown beside it because alone it flatters shedding.",
        attainment,
    )
    if any(stats[x].ttft_p95 for x in labels):
        _fig(
            "ttft_p95",
            "Tail latency of what was served",
            "Served requests only. A policy that refused most of the traffic "
            "can show an excellent tail here while failing the chart above.",
            ttft,
        )
    if any(stats[x].rejects for x in labels):
        _fig(
            "reject_reasons",
            "Why requests were refused",
            "From the policy itself, not inferred. A single reason across a "
            "whole run usually means the policy only ever saw one condition.",
            rejects,
        )
    return figures, []


# --- assembly --------------------------------------------------------------


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _provenance(bundles: list[Bundle]) -> dict[str, Any]:
    """What produced these numbers. Without it a result cannot be reproduced,
    and two results cannot honestly be compared."""
    latest = max(bundles, key=lambda b: b.manifest.get("created_at") or "")
    m = latest.manifest
    infra = (m.get("config") or {}).get("infra") or {}
    engine = infra.get("engine") or {}
    workload = (m.get("config") or {}).get("workload") or {}
    env = m.get("environment") or {}
    return {
        "model": infra.get("model"),
        "gpu": f"{infra.get('gpu')} x{infra.get('gpu_count', 1)}",
        "provider": infra.get("provider"),
        "engine": ", ".join(f"{k}={v}" for k, v in sorted(engine.items())) or None,
        "workload": ", ".join(f"{k}={v}" for k, v in sorted(workload.items())) or None,
        "slo_mode": m.get("slo_mode"),
        "baseline": m.get("baseline"),
        "repeats": m.get("repeats"),
        "seed": m.get("seed"),
        "engine_version": (env.get("engine") or {}).get("version"),
        "python": (env.get("local") or {}).get("python"),
        "packages": (env.get("local") or {}).get("packages"),
        "git_sha": _git_sha(),
        "created_at": m.get("created_at"),
    }


def build(root: Path, *, now: str = "") -> Report | None:
    """Assemble the report model. Returns None when there is nothing to report."""
    bundles = load_bundles(root)
    if not bundles:
        return None

    groups: dict[str, list[Bundle]] = {}
    for b in bundles:
        groups.setdefault(b.deployment or "ungrouped", []).append(b)

    notes: list[str] = []
    sections = []
    for deployment in sorted(groups):
        stats = _stats(groups[deployment])
        headline, detail, tone = _verdict(stats)
        figures, skipped = _figures(stats)
        notes.extend(n for n in skipped if n not in notes)
        sections.append(
            Section(
                deployment=deployment,
                headline=headline,
                detail=detail,
                tone=tone,
                caveats=_caveats(stats),
                figures=figures,
            )
        )

    if len(groups) > 1:
        notes.append(
            "This experiment covers several deployments. They are reported "
            "separately and must not be read across: a row from an A10G and a "
            "row from an A100 differ by the machine, not the policy."
        )

    unavailable: dict[str, str] = {}
    for b in bundles:
        unavailable.update(b.summary.get("unavailable") or {})

    return Report(
        experiment=bundles[0].manifest.get("experiment") or root.name,
        generated_at=now or (bundles[-1].manifest.get("created_at") or ""),
        sections=sections,
        table=compare_dir(root) or "",
        provenance=_provenance(bundles),
        unavailable=unavailable,
        notes=notes,
    )


# --- rendering -------------------------------------------------------------

_CSS = f"""
  body {{ font-family: {FONT}; color: {INK}; background: #FFFFFF;
         max-width: 54rem; margin: 0 auto; padding: 2.5rem 1.5rem 5rem;
         line-height: 1.55; }}
  h1 {{ font-size: 1.7rem; margin: 0; letter-spacing: -0.02em; }}
  h2 {{ font-size: 1.15rem; margin: 2.6rem 0 0.8rem; letter-spacing: -0.01em;
        border-bottom: 2px solid {YELLOW}; display: inline-block;
        padding-bottom: 0.2rem; }}
  .sub {{ color: {MUTED}; font-size: 0.86rem; margin-top: 0.3rem; }}
  .verdict {{ border-left: 4px solid {YELLOW}; background: #FAFAFA;
              padding: 1rem 1.2rem; margin: 1.2rem 0; border-radius: 3px; }}
  .verdict.flat {{ border-left-color: {MUTED}; }}
  .verdict.warn {{ border-left-color: {RED}; }}
  .verdict .h {{ font-weight: 700; font-size: 1.08rem; }}
  .verdict .d {{ color: #444; font-size: 0.9rem; margin-top: 0.35rem; }}
  .caveat {{ border: 1px solid #EEDDAA; background: #FFFBEF;
             padding: 0.7rem 0.9rem; margin: 0.5rem 0; border-radius: 3px;
             font-size: 0.88rem; }}
  figure {{ margin: 1.6rem 0; }}
  figure img {{ width: 100%; border: 1px solid #EAEAEA; border-radius: 3px; }}
  figcaption {{ color: {MUTED}; font-size: 0.82rem; margin-top: 0.45rem; }}
  .figtitle {{ font-weight: 700; font-size: 0.95rem; margin-bottom: 0.4rem; }}
  pre {{ background: #FAFAFA; border: 1px solid #EAEAEA; border-radius: 3px;
         padding: 1rem; overflow-x: auto; font-size: 0.78rem; line-height: 1.45; }}
  table.prov {{ border-collapse: collapse; font-size: 0.85rem; width: 100%; }}
  table.prov td {{ border-bottom: 1px solid #EEEEEE; padding: 0.4rem 0.6rem;
                   vertical-align: top; }}
  table.prov td:first-child {{ color: {MUTED}; width: 12rem; }}
  code {{ background: #F3F3F3; padding: 0.05rem 0.28rem; border-radius: 2px;
          font-size: 0.85em; }}
  @media print {{ body {{ max-width: none; }} figure {{ page-break-inside: avoid; }} }}
"""


def render_html(report: Report) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value))

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>AdmitPerf — {esc(report.experiment)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{esc(report.experiment)}</h1>",
        "<div class='sub'>AdmitPerf admission-control benchmark"
        + (f" — {esc(report.generated_at)}" if report.generated_at else "")
        + "</div>",
    ]

    for note in report.notes:
        parts.append(f"<div class='caveat'>{note}</div>")

    for section in report.sections:
        if len(report.sections) > 1 or section.deployment != "ungrouped":
            parts.append(f"<h2>{esc(section.deployment)}</h2>")
        parts.append(
            f"<div class='verdict {section.tone}'>"
            f"<div class='h'>{section.headline}</div>"
            f"<div class='d'>{section.detail}</div></div>"
        )
        for caveat in section.caveats:
            parts.append(f"<div class='caveat'>{caveat}</div>")
        for fig in section.figures:
            b64 = base64.b64encode(fig.png).decode()
            parts.append(
                f"<figure><div class='figtitle'>{esc(fig.title)}</div>"
                f"<img alt='{esc(fig.title)}' src='data:image/png;base64,{b64}'>"
                f"<figcaption>{esc(fig.caption)}</figcaption></figure>"
            )

    if report.table:
        parts.append("<h2>Every number</h2>")
        parts.append(f"<pre>{esc(report.table)}</pre>")

    parts.append("<h2>What produced this</h2><table class='prov'>")
    for key, value in report.provenance.items():
        if value in (None, "", {}):
            continue
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in sorted(value.items()) if v)
        parts.append(f"<tr><td>{esc(key.replace('_', ' '))}</td><td>{esc(value)}</td></tr>")
    parts.append("</table>")

    if report.unavailable:
        parts.append("<h2>Not measured</h2>")
        parts.append(
            "<div class='sub'>Named in the metric set but not produced here. "
            "Listed with the reason rather than estimated, so a gap cannot be "
            "mistaken for a zero.</div><table class='prov'>"
        )
        for metric, reason in sorted(report.unavailable.items()):
            parts.append(f"<tr><td>{esc(metric)}</td><td>{esc(reason)}</td></tr>")
        parts.append("</table>")

    parts.append("</body></html>")
    return "\n".join(parts)


def write(root: Path, *, out_dir: Path | None = None, now: str = "") -> Path | None:
    """Write `report.html`, `compare.txt` and `figures/` under the results dir.

    The PNGs are written alongside the self-contained HTML on purpose: the HTML
    is for reading, the loose files are for dropping into LaTeX.
    """
    report = build(root, now=now)
    if report is None:
        return None

    target = out_dir or root
    target.mkdir(parents=True, exist_ok=True)
    (target / "compare.txt").write_text(report.table + "\n")

    figures = target / "figures"
    for section in report.sections:
        for fig in section.figures:
            figures.mkdir(parents=True, exist_ok=True)
            stem = f"{section.deployment}-{fig.name}" if len(report.sections) > 1 else fig.name
            (figures / f"{stem}.png").write_bytes(fig.png)

    path = target / "report.html"
    path.write_text(render_html(report))
    return path


__all__ = ["Bundle", "Figure", "Report", "Section", "build", "load_bundles", "render_html", "write"]
