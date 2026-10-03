"""Fleet fields on InfraConfig, and the promise that Modal configs are untouched.

The second half matters more than the first. Adding a provider is only safe if
every config written against the old one still loads and still validates, so
that test walks the real `experiments/` directory rather than a fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from admitperf.core.config import ConfigError, ExperimentConfig, InfraConfig

REPO = Path(__file__).resolve().parents[1]
EXPERIMENTS = REPO / "experiments"


def _configs() -> list[Path]:
    """Every runnable experiment config.

    `archive/` is excluded: those described Modal deployments and Modal is gone.
    They are kept because their results are cited evidence and a figure whose
    config has been deleted is untraceable — see experiments/archive/README.md.
    """
    return sorted(
        p for p in EXPERIMENTS.rglob("*.yaml") if p.is_file() and "archive" not in p.parts
    )


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


def test_defaults_are_a_single_cluster_worker() -> None:
    """Cluster is the only provider now. Modal was removed: a hosted provider
    cannot demonstrate placement, a KV hop or a multi-worker fleet."""
    infra = InfraConfig()
    assert infra.provider == "cluster"
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


def test_the_only_provider_is_our_own_cluster() -> None:
    """Modal is gone, and its name must not quietly validate."""
    assert frozenset({"cluster"}) == InfraConfig.PROVIDERS
    with pytest.raises(ConfigError, match="provider must be one of"):
        InfraConfig(provider="modal").validate()
