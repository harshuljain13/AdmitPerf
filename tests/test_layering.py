"""Architectural boundaries, enforced rather than intended.

This file was asleep. It set

    SRC = Path(__file__).resolve().parents[1] / "admitperf"

which is `<repo>/admitperf`, while the package lives at `<repo>/src/admitperf`.
Every rule globbed zero files, the offender list was always empty, and the suite
stayed green throughout the period the boundaries it guards were being violated.

A structural test that cannot find the source is worse than no test: it reports
health it never checked. So the path is asserted first now, and the rules that
replace the old ones are the ones that actually matter — AdmitPerf installs into
someone else's gateway, so it must not reach for their infrastructure or ours.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "admitperf"


def test_the_source_tree_is_where_this_file_thinks_it_is() -> None:
    """Guards every rule below."""
    assert SRC.is_dir(), SRC
    assert list(SRC.rglob("*.py")), "no package files found — the rules below are vacuous"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_the_package_does_not_import_infra() -> None:
    """`infra/` is one worked example of a host, not a dependency.

    The moment the package imports it, "bring your own infra" is false and
    AdmitPerf only works for people who adopted our cluster layout.
    """
    offenders = [
        f"{py.relative_to(REPO)} imports {mod}"
        for py in sorted(SRC.rglob("*.py"))
        for mod in _imports(py)
        if mod == "infra" or mod.startswith("infra.")
    ]
    assert not offenders, "\n".join(offenders)


def test_the_package_does_not_name_infra_paths_either() -> None:
    """An import is not the only coupling. A helper returning `<repo>/infra/config`
    is the same dependency expressed as a string, and equally fatal to a host that
    has no such directory."""
    offenders = [
        f"{py.relative_to(REPO)}:{i}: {line.strip()}"
        for py in sorted(SRC.rglob("*.py"))
        for i, line in enumerate(py.read_text().splitlines(), 1)
        if '"infra' in line or "'infra" in line or "/ infra" in line
    ]
    assert not offenders, "\n".join(offenders)


def test_infra_does_not_ship_in_the_wheel() -> None:
    """Otherwise `pip install admitperf` also installs Kubernetes manifests and
    Grafana dashboards."""
    pyproject = (REPO / "pyproject.toml").read_text()
    assert 'packages = ["src/admitperf"]' in pyproject
    assert '"infra"' not in pyproject.split("[tool.ruff]")[0]


def test_the_harness_is_gone() -> None:
    """Named individually so re-adding one is a decision, not a drift.

    Each of these existed because AdmitPerf was a benchmark harness that owned a
    cluster. Under "bring your own infra" every one of them is someone else's job,
    and keeping any would mean the replacement gets built next to a second engine
    adapter and a second run path.
    """
    gone = [
        "bench",
        "traces",
        "reports",
        "core/engine.py",
        "core/state.py",
        "core/ports.py",
        "core/config.py",
        "core/runner.py",
        "core/session.py",
        "policies/chronos",
    ]
    present = [p for p in gone if (SRC / p).exists()]
    assert not present, f"the harness is back: {present}"
