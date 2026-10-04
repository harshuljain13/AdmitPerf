"""The seven reporting items, evaluated against one run.

From the survey (§8.1). All seven concern disclosure of what was done rather than
prescription of what to run, which is why a report can check them at all.

The finding the survey reports: of seven columns across sixteen admission-primary
papers, six are fully populated and the seventh — **signal liveness** — is empty for
every one of them. So item 3 is the one AdmitPerf exists to fill, and the others are
here to say plainly what this log can and cannot evidence rather than leaving a reader
to assume.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from admitperf.item import Item
from admitperf.status import Status

if TYPE_CHECKING:
    from admitperf.report import Report

ASKS = {
    1: "Offered load expressed as a multiple of the deployment's measured serveable "
    "capacity, rather than as a raw arrival rate or an unquantified “high load”. "
    "Two papers reporting the same arrival rate on different hardware may be operating "
    "in different regimes.",
    2: "SLO targets expressed relative to unloaded latency on the same model and "
    "hardware. The difficulty of meeting a fixed millisecond target varies with the "
    "deployment, so absolute targets are not comparable across papers.",
    3: "The observed range of the quantity the policy reads, over the evaluation. "
    "Without it, a policy whose signal never approached its threshold cannot be "
    "distinguished from one that evaluated a varying signal and declined to reject.",
    4: "The number of repeats and the observed spread, rather than a single run or a median alone.",
    5: "For each parameter, whether it was fitted on the evaluated deployment, "
    "inherited from the source paper, or left at a default. Policies carrying "
    "hardware-dependent cost constants cannot otherwise be reproduced.",
    6: "The goodput denominator in particular: whether rejected requests count as "
    "misses or are excluded. The two conventions yield different numbers for the same "
    "run, and the difference grows with the rejection rate.",
    7: "Model, engine version, hardware, workload subset, offered load, SLO tuple, and "
    "the identity of the baseline actually run.",
}


def evaluate(report: Report, *, repeats: int = 1, has_baseline: bool | None = None) -> list[Item]:
    """Check one run against all seven.

    `repeats` and `has_baseline` come from the experiment, not the log, because items 4
    and 7 are properties of a set of runs rather than of any single one.
    """
    out: list[Item] = []

    def add(n: int, name: str, status: Status, detail: str) -> None:
        out.append(Item(n, name, status, detail, ASKS[n]))

    # 1 — capacity-relative load
    add(
        1,
        "Capacity-relative load",
        Status.UNEVIDENCED,
        "No measured serveable capacity is recorded, so the offered load cannot be "
        "expressed as a multiple of it. A raw rate is not comparable across hardware.",
    )

    # 2 — calibrated deadlines
    add(
        2,
        "Calibrated deadlines",
        Status.UNEVIDENCED,
        "No unloaded-latency baseline is recorded, so an SLO here cannot be expressed "
        "as a multiple of what this deployment does when nothing is queued.",
    )

    # 3 — signal liveness. The one the corpus never fills, and the one we can.
    sig = report.watched_signal()
    if sig is None:
        add(
            3,
            "Signal liveness",
            Status.UNEVIDENCED,
            "No signal was supplied, so whether the policy could have fired is unknown. "
            "Correct for a baseline, which reads nothing by design.",
        )
    else:
        name, rng, thr = sig
        if thr is None:
            add(
                3,
                "Signal liveness",
                Status.OK,
                f"`{name}` ranged {rng['min']:.3g} to {rng['max']:.3g} over "
                f"{rng['samples']} decisions. No threshold to judge it against.",
            )
        elif rng["max"] >= thr:
            add(
                3,
                "Signal liveness",
                Status.OK,
                f"`{name}` reached {rng['max']:.3g} against a threshold of {thr:.3g}, "
                f"so the policy had the chance to act. Range {rng['min']:.3g}–"
                f"{rng['max']:.3g} over {rng['samples']} decisions.",
            )
        else:
            short = 1 - (rng["max"] / thr) if thr else 1.0
            add(
                3,
                "Signal liveness",
                Status.FAIL,
                f"`{name}` peaked at {rng['max']:.3g} against a threshold of "
                f"{thr:.3g} — {short:.0%} short. The policy could not have fired, so "
                "this run is indistinguishable from no policy at all.",
            )

    # 4 — repeats and spread
    if repeats >= 3:
        add(4, "Repeats and spread", Status.OK, f"{repeats} repeats, with the spread reported.")
    elif repeats == 2:
        add(
            4,
            "Repeats and spread",
            Status.FAIL,
            "Two repeats give a range but not a spread worth quoting. Three is the usual minimum.",
        )
    else:
        add(
            4,
            "Repeats and spread",
            Status.FAIL,
            "One run, so there is no spread. A gap smaller than the run-to-run "
            "variation is not a result, and with one run that variation is unknown.",
        )

    # 5 — parameter provenance
    params = report.params
    provenance = report.provenance
    if not params:
        add(5, "Parameter provenance", Status.OK, "The policy takes no parameters.")
    elif provenance and set(provenance) >= set(params):
        shown = ", ".join(f"{k}={params[k]} ({provenance[k]})" for k in sorted(params))
        add(5, "Parameter provenance", Status.OK, shown)
    else:
        missing = sorted(set(params) - set(provenance or {}))
        add(
            5,
            "Parameter provenance",
            Status.UNEVIDENCED,
            f"Values are recorded ({', '.join(f'{k}={v}' for k, v in sorted(params.items()))}) "
            f"but not where they came from. Undeclared: {', '.join(missing)}. Pass "
            "`provenance={'threshold': 'fitted'}` — fitted, inherited, or default.",
        )

    # 6 — metric definition
    add(
        6,
        "Metric definition",
        Status.OK,
        "Goodput counts met-SLO requests over everything OFFERED, so a refusal is a "
        "miss. The alternative denominator rewards refusing more rather than better.",
    )

    # 7 — configuration disclosure
    missing_7 = []
    if not report.notes:
        missing_7.append("model, engine version, hardware and workload (pass `notes=`)")
    if has_baseline is False:
        missing_7.append("the identity of the baseline actually run")
    if report.offered_rps() is None:
        missing_7.append("offered load")
    if missing_7:
        add(
            7,
            "Configuration disclosure",
            Status.UNEVIDENCED,
            "Not recorded: " + "; ".join(missing_7) + ".",
        )
    else:
        add(7, "Configuration disclosure", Status.OK, report.notes or "")

    return out


def summary(items: list[Item]) -> str:
    counts = {s: sum(1 for i in items if i.status is s) for s in Status}
    return (
        f"{counts[Status.OK]} evidenced · {counts[Status.FAIL]} failed · "
        f"{counts[Status.UNEVIDENCED]} not recorded"
    )
