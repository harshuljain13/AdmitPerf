"""The CLI, exercised rather than inspected.

This file exists because `admitperf compare --experiment` shipped with an
`AttributeError` in it — a rename that missed one call site — and the whole suite
stayed green, because nothing ran the command. A command with no test is a command
whose first user is the test.
"""

from __future__ import annotations

from click.testing import CliRunner

from admitperf.cli import main
from admitperf.core.log import Log
from admitperf.policies import KvThreshold, NoAdmission

BUSY = {"vllm:kv_cache_usage_perc": 0.95, "vllm:num_requests_waiting": 40.0}
IDLE = {"vllm:kv_cache_usage_perc": 0.05, "vllm:num_requests_waiting": 0.0}


def _experiment(root, name="exp", runs=2):
    """A baseline and one policy, named, with outcomes — the smallest comparable set."""
    ident = {"experiment": name, "notes": "a fixture"}
    for repeat in range(1, runs + 1):
        for sub, build in (
            ("no_admission", lambda log, r: NoAdmission(log=log, run=r, **ident)),
            ("kv_threshold", lambda log, r: KvThreshold(threshold=0.9, log=log, run=r, **ident)),
        ):
            d = root / "experiments" / name / sub
            d.mkdir(parents=True, exist_ok=True)
            with build(str(d / f"r{repeat}.jsonl"), f"r{repeat}") as p:
                for i in range(12):
                    rid = f"q{i}"
                    if p(BUSY if i % 2 else IDLE, request_id=rid).admitted:
                        p.outcome(rid, ttft_ms=100.0 + i * 10, ok=i < 8)


def _run(tmp_path, monkeypatch, *args):
    monkeypatch.chdir(tmp_path)
    return CliRunner().invoke(main, list(args))


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_experiments_lists_what_was_found(tmp_path, monkeypatch) -> None:
    _experiment(tmp_path)
    result = _run(tmp_path, monkeypatch, "experiments")
    assert result.exit_code == 0, result.output
    assert "exp" in result.output
    assert "no_admission ×2  (baseline)" in result.output
    assert "a fixture" in result.output


def test_experiments_says_what_to_run_when_there_is_nothing(tmp_path, monkeypatch) -> None:
    """The first thing a new user types. It must name the command that makes something."""
    result = _run(tmp_path, monkeypatch, "experiments")
    assert result.exit_code == 0
    assert "admitperf demo" in result.output


def test_an_experiment_without_a_baseline_is_refused_with_the_reason(tmp_path, monkeypatch) -> None:
    """Comparing two policies against each other answers a different question, and
    "the policy refused 27%" has nothing to be 27% of without a baseline."""
    d = tmp_path / "experiments" / "solo" / "kv_threshold"
    d.mkdir(parents=True)
    with KvThreshold(threshold=0.9, log=str(d / "r1.jsonl"), experiment="solo", run="r1") as p:
        for i in range(6):
            p(BUSY, request_id=f"q{i}")

    result = _run(tmp_path, monkeypatch, "compare", "--experiment", "solo")
    assert result.exit_code != 0
    assert "no baseline" in result.output
    assert "NoAdmission" in result.output, "should name a policy that would fix it"


def test_an_unknown_experiment_lists_the_known_ones(tmp_path, monkeypatch) -> None:
    """ "No such experiment" is useless alone — the point of naming things is being able
    to ask for one by name and be told what the names are."""
    _experiment(tmp_path)
    result = _run(tmp_path, monkeypatch, "compare", "--experiment", "typo")
    assert result.exit_code != 0
    assert "exp" in str(result.output) + str(result.exception)


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------


def test_compare_by_experiment_name(tmp_path, monkeypatch) -> None:
    """The regression this file was written for: this path raised AttributeError from a
    rename that missed a call site, and no test ran it."""
    _experiment(tmp_path)
    result = _run(tmp_path, monkeypatch, "compare", "--experiment", "exp")
    assert result.exit_code == 0, result.output
    assert "no_admission" in result.output and "kv_threshold" in result.output
    assert "what did the policy buy" in result.output


def test_compare_by_two_paths(tmp_path, monkeypatch) -> None:
    _experiment(tmp_path)
    base = str(tmp_path / "experiments" / "exp" / "no_admission")
    pol = str(tmp_path / "experiments" / "exp" / "kv_threshold")
    result = _run(tmp_path, monkeypatch, "compare", base, pol)
    assert result.exit_code == 0, result.output
    assert "SIDE BY SIDE" in result.output


def test_compare_with_neither_paths_nor_experiment_explains_itself(tmp_path, monkeypatch) -> None:
    result = _run(tmp_path, monkeypatch, "compare")
    assert result.exit_code != 0
    assert "--experiment" in result.output


# --------------------------------------------------------------------------
# report, signals, policies
# --------------------------------------------------------------------------


def test_report_reads_one_log(tmp_path, monkeypatch) -> None:
    _experiment(tmp_path)
    log = str(tmp_path / "experiments" / "exp" / "kv_threshold" / "r1.jsonl")
    result = _run(tmp_path, monkeypatch, "report", log)
    assert result.exit_code == 0, result.output
    assert "THE FINDING" in result.output


def test_report_check_fails_on_a_log_that_cannot_support_a_claim(tmp_path, monkeypatch) -> None:
    """For CI: a run whose policy never fired should not pass as evidence."""
    d = tmp_path / "experiments" / "quiet" / "kv_threshold"
    d.mkdir(parents=True)
    with KvThreshold(threshold=0.9, log=str(d / "r1.jsonl"), experiment="quiet", run="r1") as p:
        for i in range(8):
            p(IDLE, request_id=f"q{i}")

    result = _run(tmp_path, monkeypatch, "report", str(d / "r1.jsonl"), "--check")
    assert result.exit_code != 0
    assert "INERT" in result.output


def test_signals_names_what_to_export(tmp_path, monkeypatch) -> None:
    """Run first, before any integration: it says what AdmitPerf can already read."""
    result = _run(tmp_path, monkeypatch, "signals")
    assert result.exit_code == 0
    assert "kv_pressure" in result.output
    assert "vllm:kv_cache_usage_perc" in result.output


def test_policies_lists_the_shipped_ones(tmp_path, monkeypatch) -> None:
    result = _run(tmp_path, monkeypatch, "policies")
    assert result.exit_code == 0
    for name in ("no_admission", "kv_threshold", "queue_depth", "dual_gate"):
        assert name in result.output


def test_demo_writes_the_documented_layout(tmp_path, monkeypatch) -> None:
    """`experiments/<experiment>/<policy>/r<n>.jsonl` — the path mirrors the identity
    the policy declared, so a report and its log can be found from each other."""
    result = _run(tmp_path, monkeypatch, "demo", "--repeats", "2")
    assert result.exit_code == 0, result.output
    log = tmp_path / "experiments" / "demo" / "no_admission" / "r2.jsonl"
    assert log.exists()
    assert Log.read(log), "the log should not be empty"
