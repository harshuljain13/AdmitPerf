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
import subprocess
import sys
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


# --------------------------------------------------------------------------
# core/ runs in someone else's request path
# --------------------------------------------------------------------------


def test_core_reaches_no_network_and_spawns_nothing() -> None:
    """Anything in `core` that opened a socket would be fetching metrics, which is
    the host's job, and would put a network round trip on an admission decision.

    The rule is scoped to core and policies on purpose, so a CLI command can scrape
    on an operator's behalf while the library never does.
    """
    forbidden = {"httpx", "requests", "urllib", "urllib.request", "socket", "subprocess"}
    offenders = [
        f"{py.relative_to(REPO)} imports {mod}"
        for py in sorted((SRC / "core").rglob("*.py")) + sorted((SRC / "policies").rglob("*.py"))
        for mod in _imports(py)
        if mod in forbidden
    ]
    assert not offenders, "\n".join(offenders)


def test_core_imports_no_third_party_package() -> None:
    """Checked by name as well as by install, because an empty dependency list only
    holds while nobody reaches for something another extra happened to pull in."""
    stdlib = set(sys.stdlib_module_names)
    offenders = []
    for py in sorted((SRC / "core").rglob("*.py")):
        for mod in _imports(py):
            if mod.split(".")[0] in stdlib or mod.startswith(("admitperf", "__future__")):
                continue
            offenders.append(f"{py.relative_to(REPO)} imports {mod}")
    assert not offenders, "\n".join(offenders)


def test_core_is_importable_without_the_cli_dependency() -> None:
    """click is a dependency of the package and deliberately not of core. A
    dependency we add there is one the host did not agree to, in the one place they
    cannot afford it."""
    code = (
        "import sys\n"
        "sys.modules['click'] = None\n"
        "from admitperf.core import Policy, Decision, Signal, Verdict, Log\n"
        "from admitperf.core.signals import ALL\n"
        "from admitperf.policies import KvThreshold\n"
        "assert ALL\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=REPO
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_engine_vocabulary_lives_only_in_signals() -> None:
    """A module that special-cases vLLM is a module that will be wrong for SGLang,
    Modal, and whatever comes next. Vendor metric names belong in the one file that
    lists them as guesses a host can override."""
    # signals.py lists them as overridable guesses. demo.py EMITS them, because the
    # thing it demonstrates is a raw vLLM scrape being converted into signals — a demo
    # that invented its own metric names would not show the conversion at all.
    allowed = {SRC / "core" / "signals.py", SRC / "demo.py"}
    offenders = [
        f"{py.relative_to(REPO)} mentions {needle}"
        for py in sorted(SRC.rglob("*.py"))
        if py not in allowed
        for needle in ("vllm:", "DCGM_FI_", "sglang:")
        if needle in py.read_text()
    ]
    assert not offenders, "\n".join(offenders)


def test_one_class_per_file() -> None:
    """A file with one class is a file whose name tells you what is in it."""
    offenders = []
    for py in sorted(SRC.rglob("*.py")):
        classes = [
            n.name for n in ast.walk(ast.parse(py.read_text())) if isinstance(n, ast.ClassDef)
        ]
        if len(classes) > 1:
            offenders.append(f"{py.relative_to(REPO)} defines {classes}")
    assert not offenders, "\n".join(offenders)


# --------------------------------------------------------------------------
# The README is the PyPI page
# --------------------------------------------------------------------------


def test_readme_images_would_render_on_pypi() -> None:
    """Both halves of why the banner was broken on the published page.

    PyPI renders this README with no repository context, so a relative path resolves
    to nothing. And it proxies images through camo, which does not serve SVG — so a
    `.svg` src is a broken image even when the URL is absolute. Neither failure shows
    up locally or on GitHub, and neither can be fixed without publishing a new version,
    because a released long_description cannot be edited.
    """
    import re

    readme = (REPO / "README.md").read_text()
    for src in re.findall(r'<img[^>]*src="([^"]+)"', readme):
        assert src.startswith("http"), f"relative image src will not resolve on PyPI: {src}"
        assert not src.endswith(".svg"), f"PyPI's image proxy does not serve SVG: {src}"


def test_every_readme_image_is_committed() -> None:
    """An absolute raw.githubusercontent URL only works if the file is actually in the
    repository at that path, and a 404 looks identical to a broken renderer."""
    import re

    readme = (REPO / "README.md").read_text()
    for src in re.findall(r'<img[^>]*src="([^"]+)"', readme):
        if "raw.githubusercontent.com" not in src:
            continue
        # .../<owner>/<repo>/<ref>/<path...>
        path = src.split("raw.githubusercontent.com/", 1)[1].split("/", 3)[3]
        assert (REPO / path).is_file(), f"README points at {path}, which is not in the repo"


# --------------------------------------------------------------------------
# No cluster in the package
# --------------------------------------------------------------------------


def test_the_repo_ships_no_infrastructure() -> None:
    """Helm charts, Kubernetes manifests and bring-up scripts lived here for a while and
    it was a mistake: a policy library that ships a cluster is a test harness pretending
    to be a library, and readers could not tell which half they were installing.

    The cluster that exercises this lives in llm-inference-experiments/admitperf_testing.
    """
    for name in ("infra", "chart", "k8s-config", "deploy", "helm"):
        assert not (REPO / name).exists(), f"{name}/ is back"


def test_the_load_generator_is_not_in_the_package() -> None:
    """Offering traffic has nothing to do with deciding whether to admit it. A load
    generator inside a policy library implies the library drives the experiment, which is
    the opposite of the claim that you call it from your own gateway.
    """
    assert not (SRC / "load.py").exists()
