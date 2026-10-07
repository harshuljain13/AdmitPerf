"""The demo must keep working.

A README that quotes a result from a script nobody runs is a README that goes stale
silently. This runs it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = ["-m", "admitperf.demo"]


def test_the_example_runs(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, *DEMO],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,  # writes its logs under the temp dir, not the repo
    )
    assert proc.returncode == 0, proc.stderr
    # One convention for everything AdmitPerf writes, pinned here because the path
    # mirrors the declared identity: experiment, then policy, then run. A separate
    # folder per tool would be a second convention for the same thing.
    assert (tmp_path / "experiments" / "demo" / "no_admission" / "r1.jsonl").exists()


def test_the_example_produces_the_finding_the_readme_quotes(tmp_path) -> None:
    """Both halves of it. The KV threshold cuts the tail and LOSES goodput; a queue
    bound derived from the SLO improves both. A latency-only report would have picked
    the first one, which is the entire argument for comparing.
    """
    proc = subprocess.run(
        [sys.executable, *DEMO], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    out = proc.stdout
    kv = out.split("### baseline  vs  kv_threshold")[1].split("###")[0]
    queue = out.split("### baseline  vs  queue_depth")[1]

    assert "x lower" in kv and "goodput is DOWN" in kv
    assert "x lower" in queue and "goodput is up" in queue


def test_repeats_produce_runs_that_actually_differ(tmp_path) -> None:
    """Arrivals are Poisson and service times jittered, so repeats of the same
    configuration differ.

    This is load-bearing. An earlier version set a seed and then never used `random`,
    so every repeat was byte-identical: the spread was zero and the comparison printed
    a clean separation on the strength of one deterministic run per policy. An error bar
    computed from identical runs is worse than no error bar.
    """
    proc = subprocess.run(
        [sys.executable, *DEMO, "--repeats", "3"],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr

    from admitperf.report import Report

    run = tmp_path / "experiments" / "demo" / "no_admission"
    maxima = {Report.from_log(p).signal_range("queue_depth")["max"] for p in run.glob("*.jsonl")}
    assert len(maxima) > 1, f"every repeat was identical: {maxima}"
    assert "(" in proc.stdout, "no spread was reported despite repeats differing"
