"""Where things are, found rather than counted.

Paths here were computed as `parents[N]`, and N was wrong in three places: the
brand assets, and both views that look for experiments. Counting parents breaks
silently every time a file moves, and a wrong path produces an empty list rather
than an error — so the page renders with nothing on it and looks like a data
problem.

Walking up for a marker cannot drift.
"""

from __future__ import annotations

from pathlib import Path

MARKER = "pyproject.toml"


def repo_root(start: Path | None = None) -> Path:
    """The directory containing pyproject.toml, walking up from `start`."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / MARKER).is_file():
            return candidate
    # Installed as a wheel, with no repo around it. The caller gets a path that
    # does not exist, which is correct: there are no experiments to find.
    return here.parent


def experiments_dir() -> Path:
    return repo_root() / "experiments"


def infra_config_dir() -> Path:
    return repo_root() / "infra" / "config"


def assets_dir() -> Path:
    return repo_root() / "assets"
