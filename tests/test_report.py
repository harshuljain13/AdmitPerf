"""The AdmitPerf Report.

Two things this file is mostly about.

The report must never flatter a run. A missing input is reported as unevidenced,
not as a pass; an inert signal fails item 3 rather than being omitted; and the
sparkline is scaled so a run that never left the floor looks like one.

And a policy author declares `signal` once. Everything else — the capability
check, the liveness verdict, the policy card, the range table — derives from it,
because four copies of the same fact is how they drift apart.
"""

from __future__ import annotations

import math

import pytest

from admitperf.core.api import AdmissionPolicy, Decision, Request, SystemState
from admitperf.core.registry import get_policy
from admitperf.report import (
    ItemStatus,
    PolicyCard,
    Verdict,
    evaluate,
    facts_from,
    report_for,
    sparkline,
    summarise,
)

# --------------------------------------------------------------------------
# One declaration, four consumers
# --------------------------------------------------------------------------


class _Spy(AdmissionPolicy):
    name = "spy"
    signal = "kv_used_fraction"
    threshold = 0.75

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()


class _TwoSignals(AdmissionPolicy):
    name = "two"
    signal = "waiting_requests"
    requires = frozenset({"waiting_requests", "running_requests"})

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()


class _NoSignal(AdmissionPolicy):
    name = "none"

    def decide(self, req: Request, state: SystemState) -> Decision:
        return Decision.admit()


def test_requires_derives_from_signal() -> None:
    """The point of the whole design: declare the signal, get the capability check."""
    assert _Spy.requires == frozenset({"kv_used_fraction"})


def test_an_explicit_requires_still_wins() -> None:
    """A policy reading two signals is not forced into the simple case."""
    assert _TwoSignals.requires == frozenset({"waiting_requests", "running_requests"})


def test_no_signal_means_no_derived_requirement() -> None:
    assert _NoSignal.requires == frozenset()


def test_read_signal_pulls_the_declared_field() -> None:
    state = SystemState(
        now=0.0,
        kv_used_fraction=0.42,
        running_requests=1,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )
    assert _Spy().read_signal(state) == pytest.approx(0.42)
    assert _NoSignal().read_signal(state) is None


def test_read_signal_survives_a_missing_value() -> None:
    """An engine that does not expose KV gives None, not a crash and not a zero.

    Treating it as zero would report an empty cache, which is the most dangerous
    possible wrong answer here: it looks like headroom.
    """
    state = SystemState(
        now=0.0,
        kv_used_fraction=None,
        running_requests=0,
        waiting_requests=0,
        running_agents=0,
        per_tenant_running={},
        per_tenant_admitted_recent={},
        engine_metrics={},
    )
    assert _Spy().read_signal(state) is None


def test_the_shipped_policies_all_declare_their_signal() -> None:
    """Every built-in should be reportable. A policy with no signal produces a
    report that cannot say whether it had the chance to act."""
    from admitperf.core.registry import available

    missing = [n for n, c in available().items() if c.signal is None and n != "no_admission"]
    assert not missing, f"policies without a declared signal: {missing}"


# --------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------


def _range(values: list[float | None], threshold: float | None = 0.90):
    return summarise(values, name="kv_used_fraction", threshold=threshold)


def test_live_when_the_signal_crosses() -> None:
    r = _range([0.1, 0.5, 0.95])
    assert r.verdict is Verdict.LIVE
    assert r.crossings == 1


def test_inert_when_the_signal_stays_far_below() -> None:
    """The half-capacity-headroom shape, which is why this module exists."""
    r = _range([0.0, 0.0012, 0.0071])
    assert r.verdict is Verdict.INERT
    assert r.crossings == 0
    assert "could not have fired" in r.explain()


def test_marginal_is_distinguished_from_inert() -> None:
    """0.88 against a 0.90 threshold did not fire, and it is a different kind of
    not-firing from peaking at 0.004. Collapsing them loses the transition point."""
    assert _range([0.5, 0.88]).verdict is Verdict.MARGINAL
    assert _range([0.5, 0.004]).verdict is Verdict.INERT


def test_unknown_when_nothing_was_recorded() -> None:
    r = _range([None, None])
    assert r.verdict is Verdict.UNKNOWN
    assert r.samples == 0
    assert r.missing == 2


def test_unknown_when_there_is_no_threshold_to_judge_against() -> None:
    """A range can be described without a threshold. It cannot be judged."""
    r = _range([0.1, 0.9], threshold=None)
    assert r.verdict is Verdict.UNKNOWN
    assert "nothing to compare" in r.explain()


def test_missing_values_are_counted_not_dropped() -> None:
    """A run where half the decisions had no signal is broken, and the count is
    the only way anyone notices."""
    r = _range([0.1, None, 0.2, None])
    assert r.samples == 2
    assert r.missing == 2


def test_headroom_says_how_far_short() -> None:
    r = _range([0.45])
    assert r.headroom == pytest.approx(0.5)
    assert "50% short" in r.explain()


def test_percentiles_are_values_the_signal_actually_took() -> None:
    """Nearest-rank, not interpolated. An interpolated p95 can report a number the
    signal never reached, which defeats the purpose of reporting the range."""
    r = _range([0.1, 0.2, 0.3, 0.4])
    for q in (r.min, r.p50, r.p95, r.p99, r.max):
        assert q in (0.1, 0.2, 0.3, 0.4)


def test_empty_range_is_nan_rather_than_zero() -> None:
    r = _range([])
    assert math.isnan(r.min) and math.isnan(r.max)


# --------------------------------------------------------------------------
# The sparkline must not make an inert run look busy
# --------------------------------------------------------------------------


def test_sparkline_scales_to_the_threshold() -> None:
    """Normalising to the series' own maximum would stretch a flat run to fill
    the row. The whole point is that it should look flat."""
    flat = sparkline([0.001, 0.002, 0.003], threshold=0.90)
    assert set(flat) <= {" ", "▁"}, flat


def test_sparkline_fills_when_the_signal_reaches_the_threshold() -> None:
    assert "█" in sparkline([0.1, 0.5, 0.9], threshold=0.90)


def test_sparkline_without_a_threshold_falls_back_to_its_own_max() -> None:
    assert "█" in sparkline([0.001, 0.002, 0.003], threshold=None)


def test_sparkline_says_so_when_there_is_nothing_to_draw() -> None:
    assert sparkline([None, None]) == "(no signal recorded)"


def test_sparkline_preserves_order_so_it_shows_the_run_not_the_distribution() -> None:
    """A sorted sparkline is a picture of the histogram, which always slopes up."""
    down = sparkline([0.9, 0.6, 0.3, 0.1], threshold=0.90)
    assert down[0] > down[-1]


# --------------------------------------------------------------------------
# The seven items, and the refusal to flatter
# --------------------------------------------------------------------------


def _facts(**kw):
    return facts_from(get_policy("kv_threshold", threshold=0.90), kw.pop("decisions", []), **kw)


def test_an_inert_signal_fails_item_three() -> None:
    facts = facts_from(
        get_policy("kv_threshold", threshold=0.90),
        [{"kv_used_fraction": 0.004, "kind": "admit"}] * 10,
    )
    item = next(i for i in evaluate(facts) if i.number == 3)
    assert item.status is ItemStatus.FAIL


def test_a_live_signal_passes_item_three() -> None:
    facts = facts_from(
        get_policy("kv_threshold", threshold=0.90),
        [{"kv_used_fraction": 0.95, "kind": "reject"}],
    )
    item = next(i for i in evaluate(facts) if i.number == 3)
    assert item.status is ItemStatus.OK


def test_a_single_run_fails_the_repeats_item() -> None:
    """One run has no error bar, so a gap against another run may be noise."""
    item = next(i for i in evaluate(_facts(repeats=1)) if i.number == 4)
    assert item.status is ItemStatus.FAIL


def test_unevidenced_items_are_not_reported_as_passing() -> None:
    """The failure mode this guards against is a flattering report: a run that
    recorded nothing should not produce a page of ticks."""
    items = evaluate(_facts())
    assert all(i.status is not ItemStatus.OK for i in items), [
        (i.number, i.status) for i in items if i.status is ItemStatus.OK
    ]


def test_unevidenced_is_distinct_from_failed() -> None:
    """Conflating them either slanders the run or flatters it."""
    items = {i.number: i.status for i in evaluate(_facts(repeats=1))}
    assert items[1] is ItemStatus.UNKNOWN  # no load recorded
    assert items[4] is ItemStatus.FAIL  # one run, actively insufficient


def test_capacity_relative_load_needs_both_numbers() -> None:
    assert next(i for i in evaluate(_facts(offered_rps=15.0)) if i.number == 1).status is (
        ItemStatus.UNKNOWN
    )
    ok = _facts(offered_rps=15.0, capacity_rps=30.0)
    item = next(i for i in evaluate(ok) if i.number == 1)
    assert item.status is ItemStatus.OK
    assert "50%" in item.detail


def test_configuration_disclosure_names_what_is_missing() -> None:
    item = next(i for i in evaluate(_facts(config_sha="abc123")) if i.number == 7)
    assert item.status is ItemStatus.UNKNOWN
    assert "commit" in item.detail


# --------------------------------------------------------------------------
# The policy card
# --------------------------------------------------------------------------


def test_card_reports_the_threshold_actually_used() -> None:
    """A card quoting the default while the run used something else is worse than
    no card at all."""
    card = PolicyCard.of(get_policy("kv_threshold", threshold=0.70))
    assert card.threshold == pytest.approx(0.70)


def test_card_defaults_cover_the_common_case() -> None:
    card = PolicyCard.of(_Spy())
    assert (card.unit, card.setting, card.portability) == ("request", "online", "A")


def test_card_flags_a_missing_signal() -> None:
    notes = PolicyCard.of(_NoSignal()).anomalies()
    assert any("no signal declared" in n for n in notes)


def test_card_flags_a_signal_absent_from_requires() -> None:
    """Exactly the drift the single declaration exists to prevent, caught for any
    policy that opts out of it."""

    class Drifted(AdmissionPolicy):
        name = "drifted"
        signal = "kv_used_fraction"
        requires = frozenset({"waiting_requests"})

        def decide(self, req: Request, state: SystemState) -> Decision:
            return Decision.admit()

    notes = PolicyCard.of(Drifted()).anomalies()
    assert any("not in requires" in n for n in notes)


def test_card_flags_a_value_outside_the_taxonomy() -> None:
    class Odd(AdmissionPolicy):
        name = "odd"
        signal = "kv_used_fraction"
        objective = "vibes"

        def decide(self, req: Request, state: SystemState) -> Decision:
            return Decision.admit()

    assert any("outside the taxonomy" in n for n in PolicyCard.of(Odd()).anomalies())


# --------------------------------------------------------------------------
# One call
# --------------------------------------------------------------------------


def test_report_for_is_one_call() -> None:
    page = report_for(
        get_policy("kv_threshold", threshold=0.90),
        [{"kv_used_fraction": 0.004, "kind": "admit"}] * 5,
        run_id="r1",
    )
    assert "AdmitPerf Report" in page
    assert "SIGNAL LIVENESS: INERT" in page


def test_the_verdict_comes_before_any_outcome_number() -> None:
    """A reader who stops after the first few lines should already know whether
    the run proves anything."""
    page = report_for(
        get_policy("kv_threshold"),
        [{"kv_used_fraction": 0.004, "kind": "admit"}],
        run_id="r1",
    )
    assert page.index("SIGNAL LIVENESS") < page.index("SEVEN REPORTING ITEMS")


def test_commit_reaches_item_seven_without_being_passed_twice() -> None:
    page = report_for(
        get_policy("kv_threshold"),
        [{"kv_used_fraction": 0.95, "kind": "reject"}],
        run_id="r1",
        commit="deadbeef1234",
        config_sha="cfg000111222",
    )
    assert "7 Configuration disclosure     ok" in page


def test_decision_records_work_as_well_as_dicts() -> None:
    """A caller is never blocked on adopting our type."""
    from admitperf.core.ports import DecisionRecord

    recs = [
        DecisionRecord(
            request_id=str(i),
            tenant_id="t",
            decided_at=0.0,
            kind="admit",
            reason=None,
            http_status=200,
            state_age_s=0.0,
            kv_used_fraction=0.5,
            waiting_requests=0,
            running_requests=1,
        )
        for i in range(3)
    ]
    facts = facts_from(get_policy("kv_threshold"), recs)
    assert facts.signal.samples == 3
    assert facts.decisions == 3


def test_a_gateway_can_log_one_generic_column() -> None:
    """`signal_value` is the fallback, so a gateway writes one column whatever
    policy happens to be loaded."""
    facts = facts_from(get_policy("kv_threshold"), [{"signal_value": 0.95, "kind": "reject"}])
    assert facts.signal.max == pytest.approx(0.95)


def test_a_run_that_refused_nothing_says_so() -> None:
    page = report_for(
        get_policy("kv_threshold"),
        [{"kv_used_fraction": 0.1, "kind": "admit"}] * 4,
        run_id="r1",
    )
    assert "no admission behaviour was exercised" in page


def test_markdown_carries_the_same_verdict() -> None:
    md = report_for(
        get_policy("kv_threshold"),
        [{"kv_used_fraction": 0.004, "kind": "admit"}],
        run_id="r1",
        markdown=True,
    )
    assert md.startswith("# AdmitPerf Report")
    assert "## Signal liveness: INERT" in md


# --------------------------------------------------------------------------
# report.json is the artifact; every other format renders from it
# --------------------------------------------------------------------------


def _payload(values=(0.004,), **kw):
    from admitperf.report import RunHeader, to_dict

    facts = facts_from(
        get_policy("kv_threshold", threshold=0.90),
        [{"kv_used_fraction": v, "kind": "admit"} for v in values],
        **kw,
    )
    return to_dict(facts, RunHeader(run_id="r1", cluster="1x A100-40"))


def test_json_is_valid_json_even_when_nothing_was_recorded() -> None:
    """A run with no signal produces NaN internally, and `json.dumps` writes bare
    NaN, which is invalid JSON that jq and every strict parser reject. So the file
    nothing could read would be exactly the one describing a broken run."""
    import json

    text = json.dumps(_payload(values=[]))
    assert "NaN" not in text
    assert json.loads(text)["signal"]["min"] is None


def test_round_trip_preserves_the_verdict() -> None:
    """from_dict has to rebuild enough that a renderer never needs the original."""
    from admitperf.report import from_dict, render

    payload = _payload(values=[0.004])
    facts, header = from_dict(payload)
    assert facts.signal.verdict is Verdict.INERT
    assert header.run_id == "r1"
    assert "INERT" in render(facts, header)


def test_round_trip_preserves_the_reporting_items() -> None:
    from admitperf.report import from_dict

    payload = _payload(values=[0.004], repeats=3, spread_p95_ms=145)
    facts, _ = from_dict(payload)
    after = {i.number: i.status for i in evaluate(facts)}
    before = {i["number"]: i["status"] for i in payload["reporting_items"]}
    assert {k: str(v) for k, v in after.items()} == before


def test_a_major_schema_mismatch_is_refused() -> None:
    """Reading a future report with today's code would produce numbers that look
    fine and mean something else."""
    from admitperf.report import from_dict

    payload = _payload()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="not compatible"):
        from_dict(payload)


def test_a_minor_schema_bump_still_reads() -> None:
    from admitperf.report import from_dict

    payload = _payload()
    payload["schema_version"] = "1.7"
    from_dict(payload)


def test_every_format_reports_the_same_verdict() -> None:
    """The reason the data is the artifact. Four renderers, one source of truth, so
    none of them can disagree about whether the run proved anything."""
    from admitperf.report import RunHeader, render_as

    facts = facts_from(
        get_policy("kv_threshold", threshold=0.90),
        [{"kv_used_fraction": 0.004, "kind": "admit"}],
    )
    header = RunHeader(run_id="r1")
    for fmt in ("json", "text", "md", "html"):
        assert "INERT" in render_as(facts, header, fmt=fmt), fmt


def test_an_unknown_format_is_refused_by_name() -> None:
    from admitperf.report import RunHeader, render_as

    facts = facts_from(get_policy("kv_threshold"), [{"kv_used_fraction": 0.1}])
    with pytest.raises(ValueError, match="unknown format"):
        render_as(facts, RunHeader(run_id="r1"), fmt="pdf")


# --------------------------------------------------------------------------
# problems() — what a CI step gates on
# --------------------------------------------------------------------------


def test_problems_names_an_inert_run() -> None:
    from admitperf.report import problems

    found = problems(_payload(values=[0.004]))
    assert any("INERT" in p for p in found)


def test_problems_is_empty_for_a_sound_run() -> None:
    from admitperf.report import problems

    payload = _payload(
        values=[0.95],
        repeats=3,
        spread_p95_ms=10,
        offered_rps=15.0,
        capacity_rps=30.0,
        deadline_ms=500,
        unloaded_ttft_ms=200,
        params_source="default",
        metric_denominator="offered",
        config_sha="abc123def456",
        commit="deadbeef1234",
    )
    assert problems(payload) == []


def test_problems_flags_a_partial_run() -> None:
    """More than 5% of decisions missing a value means the range is computed from
    a fraction of the run, which is not the same as the run."""
    from admitperf.report import RunHeader, problems, to_dict

    facts = facts_from(
        get_policy("kv_threshold"),
        [{"kv_used_fraction": 0.95}] + [{"other": 1}] * 9,
    )
    found = problems(to_dict(facts, RunHeader(run_id="r1")))
    assert any("no signal value" in p for p in found)


# --------------------------------------------------------------------------
# HTML must survive being emailed
# --------------------------------------------------------------------------


def test_html_is_self_contained() -> None:
    """No stylesheet, no CDN, no image files. A report that needs the network is a
    report that breaks when attached to an issue or opened in two years."""
    from admitperf.report import render_html

    html = render_html(_payload())
    assert "<link" not in html
    assert "http://" not in html and "https://" not in html
    assert "<style>" in html and "<svg" in html


def test_html_escapes_what_it_interpolates() -> None:
    from admitperf.report import render_html

    payload = _payload()
    payload["run"]["model"] = "<script>alert(1)</script>"
    html = render_html(payload)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_html_colours_an_inert_run_as_a_failure() -> None:
    from admitperf.report import render_html

    assert "#c92a2a" in render_html(_payload(values=[0.004]))


def test_html_handles_a_run_with_no_signal() -> None:
    from admitperf.report import render_html

    assert "no signal recorded" in render_html(_payload(values=[]))
