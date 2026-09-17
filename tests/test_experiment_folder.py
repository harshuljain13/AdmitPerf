"""One folder per experiment, and results that cannot be silently destroyed.

Both rules were bought with a loss: a second run wrote into the same results
directory as the first and replaced the only copy of a measurement we had
already reasoned about. Nothing in the tool noticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from admitperf.bench.experiment import (
    ResultsExistError,
    guard_results_dir,
    results_dir_for,
)
from admitperf.core.config import ConfigError, ExperimentConfig

CONFIG = """
name: folded
workload:
  rate: 4.0
policies:
  - name: no_admission
"""


def _experiment(tmp_path: Path, name: str = "an-experiment") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / ExperimentConfig.FILENAME).write_text(CONFIG)
    return folder


def test_a_folder_can_be_loaded_like_a_config(tmp_path: Path) -> None:
    folder = _experiment(tmp_path)
    assert ExperimentConfig.load(folder).name == "folded"


def test_a_folder_without_a_config_says_what_it_wanted(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ConfigError, match=ExperimentConfig.FILENAME):
        ExperimentConfig.load(empty)


def test_results_default_into_the_experiment_folder(tmp_path: Path) -> None:
    """The point of the folder is that everything about one experiment is in
    one place; results written elsewhere is how runs got scattered."""
    folder = _experiment(tmp_path)
    assert results_dir_for(folder, None) == folder / "results"
    assert results_dir_for(folder / ExperimentConfig.FILENAME, None) == folder / "results"


def test_an_explicit_out_still_wins(tmp_path: Path) -> None:
    folder = _experiment(tmp_path)
    assert results_dir_for(folder, tmp_path / "elsewhere") == tmp_path / "elsewhere"


def test_a_loose_config_has_no_default(tmp_path: Path) -> None:
    """A YAML sitting on its own is not an experiment folder, so the run keeps
    the timestamped default rather than inventing a home for it."""
    loose = tmp_path / "loose.yaml"
    loose.write_text(CONFIG)
    assert results_dir_for(loose, None) is None


def test_writing_over_existing_runs_is_refused(tmp_path: Path) -> None:
    """The failure this exists to prevent."""
    results = tmp_path / "results" / "no_admission-r1"
    results.mkdir(parents=True)
    (results / "manifest.json").write_text("{}")

    with pytest.raises(ResultsExistError, match="already holds"):
        guard_results_dir(tmp_path / "results")


def test_force_allows_it(tmp_path: Path) -> None:
    results = tmp_path / "results" / "no_admission-r1"
    results.mkdir(parents=True)
    (results / "manifest.json").write_text("{}")

    guard_results_dir(tmp_path / "results", force=True)


def test_an_empty_or_missing_directory_is_fine(tmp_path: Path) -> None:
    guard_results_dir(tmp_path / "nothing-here")
    (tmp_path / "empty").mkdir()
    guard_results_dir(tmp_path / "empty")
