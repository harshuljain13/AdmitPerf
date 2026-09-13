"""Task 1.6 — enforce the package split (spec D7) with a test, not good intentions.

`core` is what an ops engineer gets from `pip install admitperf`. The moment it
imports `bench`, that person starts installing a results-bundle writer and a
trace loader to make one admission decision. Interface Segregation expressed as
a dependency graph only holds if something checks it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "admitperf"

# core may not import these sibling packages.
FORBIDDEN_FOR_CORE = {"admitperf.bench", "admitperf.engines", "admitperf.runtime"}


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_core_does_not_import_heavier_layers() -> None:
    offenders: list[str] = []
    for py in (SRC / "core").rglob("*.py"):
        for mod in _imports_of(py):
            if any(mod == f or mod.startswith(f + ".") for f in FORBIDDEN_FOR_CORE):
                offenders.append(f"{py.relative_to(SRC)} imports {mod}")
    assert not offenders, "core must stay dependency-light:\n" + "\n".join(offenders)


def test_core_imports_without_optional_dependencies() -> None:
    """`import admitperf` must work with only stdlib + click available.

    Run in a subprocess with the bench/engine third-party modules blocked, so a
    stray `import httpx` in core fails here rather than in a user's minimal
    install.
    """
    code = (
        "import sys\n"
        "for blocked in ('httpx', 'yaml', 'numpy', 'pandas'):\n"
        "    sys.modules[blocked] = None\n"
        "import admitperf\n"
        "from admitperf import AdmissionPolicy, Decision, Request, SystemState\n"
        "from admitperf.core import VirtualClock, WallClock, check_compatibility\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"core needs an optional dep:\n{proc.stderr}"
    assert "ok" in proc.stdout
