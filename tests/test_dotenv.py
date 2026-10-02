"""Loading `.env`.

`modal deploy` inherits the CLI's environment, so a token that lives only in a
file never reaches the container. Sourcing it by hand works at a shell and not
at all from the dashboard, which launches these commands itself — which is how
an `HF_TOKEN` that was plainly there behaved as if it were missing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from admitperf.cli import load_dotenv


def _env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body)
    return path


def test_keys_reach_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    load_dotenv(_env(tmp_path, "HF_TOKEN=hf_abc123\n"))
    assert os.environ["HF_TOKEN"] == "hf_abc123"


def test_an_explicit_value_is_not_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`HF_TOKEN=... admitperf infra up` should not lose to a stale file."""
    monkeypatch.setenv("HF_TOKEN", "from-the-shell")
    load_dotenv(_env(tmp_path, "HF_TOKEN=from-the-file\n"))
    assert os.environ["HF_TOKEN"] == "from-the-shell"


def test_comments_blanks_exports_and_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("A", raising=False)
    monkeypatch.delenv("B", raising=False)
    loaded = load_dotenv(
        _env(tmp_path, '\n# a comment\nexport A="quoted"\nB=plain\n# HF_TOKEN=commented\n')
    )
    assert set(loaded) == {"A", "B"}
    assert os.environ["A"] == "quoted"
    assert os.environ["B"] == "plain"


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """Most installs have no .env, and none of this is required to run."""
    assert load_dotenv(tmp_path / "nope.env") == []


def test_the_deploy_says_which_modal_identity_it_will_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MODAL_TOKEN_ID` silently outranks ~/.modal.toml. When it comes from a
    file, "add a payment method" for an account you know is funded is a long
    way to walk before suspecting the wrong workspace."""
    from admitperf import cli

    monkeypatch.setattr(cli, "DOTENV_KEYS", ["MODAL_TOKEN_ID"])
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-abcdefgh12345")
    note = cli._auth_note()
    assert ".env" in note
    assert "ak-abcde" in note
    assert "ak-abcdefgh12345" not in note, "the full token should not be echoed"

    monkeypatch.delenv("MODAL_TOKEN_ID")
    assert cli._auth_note() == "auth: ~/.modal.toml"
