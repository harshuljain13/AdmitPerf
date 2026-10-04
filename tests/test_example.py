"""The working example must keep working.

A README that quotes a result from a script nobody runs is a README that goes stale
silently. This runs it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "compare_two_policies.py"


def test_the_example_runs(tmp_path) -> None:
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,  # writes its logs under the temp dir, not the repo
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "example-runs" / "baseline.jsonl").exists()


def test_the_example_produces_the_finding_the_readme_quotes(tmp_path) -> None:
    """Both halves of it. The KV threshold cuts the tail and LOSES goodput; a queue
    bound derived from the SLO improves both. A latency-only report would have picked
    the first one, which is the entire argument for comparing.
    """
    proc = subprocess.run(
        [sys.executable, str(EXAMPLE)], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    out = proc.stdout
    kv = out.split("### baseline  vs  kv_threshold")[1].split("###")[0]
    queue = out.split("### baseline  vs  queue_depth")[1]

    assert "x lower" in kv and "goodput is DOWN" in kv
    assert "x lower" in queue and "goodput is up" in queue
