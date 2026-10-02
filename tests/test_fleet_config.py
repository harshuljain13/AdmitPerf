"""Fleet fields on InfraConfig, and the promise that Modal configs are untouched.

The second half matters more than the first. Adding a provider is only safe if
every config written against the old one still loads and still validates, so
that test walks the real `experiments/` directory rather than a fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.config import ConfigError, ExperimentConfig, InfraConfig

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTS = REPO / "experiments"


def _configs() -> list[Path]:
    return sorted(p for p in EXPERIMENTS.rglob("*.yaml") if p.is_file())


def test_experiments_directory_is_not_empty() -> None:
    """Guard the guard: a glob that silently matches nothing proves nothing."""
    assert _configs(), f"no experiment configs found under {EXPERIMENTS}"


@pytest.mark.parametrize("path", _configs(), ids=lambda p: p.parent.name + "/" + p.name)
def test_shipped_config_still_loads_and_validates(path: Path) -> None:
    """Every config in the repo survives the fleet fields being added."""
    cfg = ExperimentConfig.load(path)
    cfg.validate()


@pytest.mark.parametrize("path", _configs(), ids=lambda p: p.parent.name + "/" + p.name)
def test_shipped_configs_are_single_worker(path: Path) -> None:
    """Defaults did not shift underneath the existing corpus."""
    cfg = ExperimentConfig.load(path)
    assert cfg.infra.workers == 1
    assert cfg.infra.is_fleet is False


def test_defaults_are_the_modal_shape() -> None:
    infra = InfraConfig()
    assert infra.provider == "modal"
    assert infra.workers == 1
    assert infra.is_fleet is False
    infra.validate()


def test_cluster_provider_accepts_a_fleet() -> None:
    infra = InfraConfig(provider="cluster", workers=2)
    infra.validate()
    assert infra.is_fleet is True


def test_unknown_provider_is_refused() -> None:
    with pytest.raises(ConfigError, match="provider must be one of"):
        InfraConfig(provider="kubernetes").validate()


def test_unknown_placement_is_refused() -> None:
    with pytest.raises(ConfigError, match="placement must be one of"):
        InfraConfig(provider="cluster", workers=2, placement="p2c").validate()


def test_zero_workers_is_refused() -> None:
    with pytest.raises(ConfigError, match="workers must be >= 1"):
        InfraConfig(workers=0).validate()


def test_modal_refuses_a_fleet_rather_than_silently_serving_one_worker() -> None:
    """The failure this guards against is a quiet one.

    Modal brings up a single container. Asking it for four workers would not
    error at deploy time — it would serve one and the results would read as a
    four-worker run. Refuse at config time instead.
    """
    with pytest.raises(ConfigError, match="cannot place a request"):
        InfraConfig(provider="modal", workers=4).validate()
