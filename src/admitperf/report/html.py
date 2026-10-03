"""HTML rendering — one self-contained file, no assets, no network.

A report that needs a stylesheet or a CDN is a report that breaks when emailed,
attached to an issue, or opened in two years. So the CSS is inline and the chart
is an SVG built from the same numbers the JSON carries.

Print it to PDF from the browser if a PDF is wanted. Adding weasyprint or a
headless Chrome to get one would make installing AdmitPerf harder for everyone, to
save one keystroke for the few who need it.

A pure function of `report.json`. Nothing here computes a verdict.
"""

from __future__ import annotations

from html import escape
from typing import Any

from admitperf.report.schema import from_dict, problems

#: Colour per verdict. Red for the two that mean the run cannot support a claim,
#: amber for a near miss, green only when the policy genuinely had its chance.
VERDICT_COLOUR = {
    "LIVE": ("#2b8a3e", "#d3f9d8"),
    "MARGINAL": ("#e67700", "#fff3bf"),
    "INERT": ("#c92a2a", "#ffe3e3"),
    "UNKNOWN": ("#495057", "#f1f3f5"),
}
STATUS_COLOUR = {"ok": "#2b8a3e", "FAIL": "#c92a2a", "n/a": "#868e96"}

CSS = """
:root { --ink:#111; --dim:#666; --line:#e9ecef; --mono: ui-monospace, "SF Mono", Menlo, monospace }
* { box-sizing: border-box }
body { margin:0; padding:2.5rem 1.5rem; background:#fafafa; color:var(--ink);
       font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, sans-serif }
main { max-width: 56rem; margin:0 auto; background:#fff; padding:2.5rem;
       border:1px solid var(--line); border-radius:10px }
h1 { font-size:1.4rem; margin:0 0 .25rem; letter-spacing:-.01em }
h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.09em; color:var(--dim);
     margin:2.25rem 0 .75rem; font-weight:600 }
.meta { font:12px/1.7 var(--mono); color:var(--dim); margin:0 0 1.75rem }
.verdict { padding:1rem 1.15rem; border-radius:8px; border-left:5px solid }
.verdict .v { font:700 1.15rem/1 var(--mono); letter-spacing:.02em }
.verdict p { margin:.45rem 0 0; font-size:.92rem }
.cols { display:grid; grid-template-columns:1fr 1fr; gap:2rem }
@media (max-width:48rem){ .cols{ grid-template-columns:1fr } }
table { width:100%; border-collapse:collapse; font-size:.9rem }
td,th { padding:.42rem .55rem; border-bottom:1px solid var(--line); text-align:left; vertical-align:top }
th { font-weight:600; color:var(--dim); font-size:.8rem }
tbody tr:last-child td { border-bottom:0 }
.k { color:var(--dim); white-space:nowrap }
.n { font-family:var(--mono); font-variant-numeric:tabular-nums }
.st { font:700 .72rem var(--mono); padding:.1rem .4rem; border-radius:4px; color:#fff; white-space:nowrap }
.note { background:#fff9db; border-left:4px solid #f08c00; padding:.7rem .9rem;
        margin:.5rem 0 0; font-size:.87rem; border-radius:0 6px 6px 0 }
footer { margin-top:2.5rem; padding-top:1rem; border-top:1px solid var(--line);
         font:11px var(--mono); color:var(--dim) }
@media print { body{background:#fff;padding:0} main{border:0;padding:0} }
"""


def _sparkline_svg(payload: dict[str, Any], *, width: int = 520, height: int = 72) -> str:
    """The signal against its threshold, as SVG.

    Rebuilt from the text sparkline's block heights rather than kept as a second
    series, so the chart and the text page cannot disagree. The y-axis is the
    threshold, not the series maximum: scaling to the maximum would stretch a flat
    run to fill the box and make an inert signal look busy.
    """
    # Guard on `samples`, not on the sparkline string. When nothing was recorded
    # that field holds the sentinel "(no signal recorded)", and a SPACE is a valid
    # block character at level zero — so parsing the sentinel drew a flat line
    # along the bottom, which reads as a measured zero rather than as no
    # measurement. Exactly the kind of flattering output this module exists to
    # prevent, found by a test rather than in review.
    if not payload["signal"].get("samples"):
        return '<p class="k">no signal recorded</p>'
    blocks = payload["signal"].get("sparkline") or ""
    levels = [" ▁▂▃▄▅▆▇█".index(c) / 8 for c in blocks if c in " ▁▂▃▄▅▆▇█"]
    if not levels:
        return '<p class="k">no signal recorded</p>'

    verdict = payload["liveness"]["verdict"]
    stroke = VERDICT_COLOUR.get(verdict, VERDICT_COLOUR["UNKNOWN"])[0]
    pad = 4
    inner = height - pad * 2
    step = width / max(1, len(levels) - 1) if len(levels) > 1 else width
    pts = " ".join(f"{i * step:.1f},{pad + inner - v * inner:.1f}" for i, v in enumerate(levels))
    thr_y = pad  # full height IS the threshold
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        'role="img" aria-label="signal over the run against its threshold">'
        f'<line x1="0" y1="{thr_y}" x2="{width}" y2="{thr_y}" stroke="#c92a2a" '
        'stroke-width="1" stroke-dasharray="4 3"/>'
        f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="2" '
        'stroke-linejoin="round"/></svg>'
        '<p class="k" style="font-size:.78rem;margin:.2rem 0 0">'
        "dashed line = threshold</p>"
    )


def render_html(payload: dict[str, Any]) -> str:
    """A complete HTML document from a `report.json` payload."""
    # Validates the schema version and proves the payload is readable, even
    # though the renderer works off the dict.
    from_dict(payload)

    run, live, pol, sig = (
        payload["run"],
        payload["liveness"],
        payload["policy"],
        payload["signal"],
    )
    fg, bg = VERDICT_COLOUR.get(live["verdict"], VERDICT_COLOUR["UNKNOWN"])
    e = escape

    meta = " · ".join(
        e(str(run[k])) for k in ("cluster", "model", "engine", "commit") if run.get(k)
    )

    rows = "".join(
        f'<tr><td class="k">{e(k)}</td><td class="n">{e(v)}</td></tr>'
        for k, v in (
            ("unit", pol["unit"]),
            ("setting", pol["setting"]),
            ("objective", pol["objective"]),
            ("signal", pol.get("signal") or "none declared"),
            ("portability", f"Class {pol['portability']}"),
        )
    )

    def num(key: str) -> str:
        v = sig.get(key)
        return "—" if v is None else f"{float(v):.4g}"

    sig_rows = "".join(
        f'<tr><td class="k">{e(k)}</td><td class="n">{v}</td></tr>'
        for k, v in (
            ("threshold", num("threshold")),
            ("min", num("min")),
            ("p50", num("p50")),
            ("p95", num("p95")),
            ("p99", num("p99")),
            ("max", num("max")),
            ("crossed", f"{sig['crossings']} of {sig['samples']} decisions"),
        )
    )
    if sig.get("missing"):
        sig_rows += (
            f'<tr><td class="k">missing</td><td class="n" style="color:#c92a2a">'
            f"{sig['missing']} decisions had no value</td></tr>"
        )

    items = "".join(
        f'<tr><td class="n">{i["number"]}</td><td>{e(i["title"])}</td>'
        f'<td><span class="st" style="background:'
        f'{STATUS_COLOUR.get(i["status"], "#868e96")}">{e(i["status"])}</span></td>'
        f"<td>{e(i['detail'])}</td></tr>"
        for i in payload.get("reporting_items", ())
    )

    d = payload["decisions"]
    notes = "".join(f'<p class="note">{e(n)}</p>' for n in problems(payload))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AdmitPerf Report — {e(run["id"])}</title>
<style>{CSS}</style></head>
<body><main>
<h1>AdmitPerf Report</h1>
<p class="meta">{e(run["id"])}{" · " + meta if meta else ""}</p>

<div class="verdict" style="border-color:{fg};background:{bg}">
  <div class="v" style="color:{fg}">SIGNAL LIVENESS: {e(live["verdict"])}</div>
  <p>{e(live["explanation"])}</p>
</div>

<h2>Signal over the run</h2>
{_sparkline_svg(payload)}

<div class="cols">
  <div><h2>Policy — {e(pol["name"])}</h2><table><tbody>{rows}</tbody></table></div>
  <div><h2>Signal — {e(sig["name"])}</h2><table><tbody>{sig_rows}</tbody></table></div>
</div>

<h2>Decisions</h2>
<table><tbody>
<tr><td class="k">total</td><td class="n">{d["total"]}</td></tr>
<tr><td class="k">rejected</td><td class="n">{d["rejected"]}</td></tr>
<tr><td class="k">deferred</td><td class="n">{d["deferred"]}</td></tr>
</tbody></table>

<h2>Seven reporting items</h2>
<table><thead><tr><th></th><th>Item</th><th></th><th>Detail</th></tr></thead>
<tbody>{items}</tbody></table>

{f"<h2>Why this run should not be cited</h2>{notes}" if notes else ""}

<footer>AdmitPerf · report schema {e(payload["schema_version"])}</footer>
</main></body></html>"""
