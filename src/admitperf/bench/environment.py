"""What produced a number, recorded alongside it.

`docs/design.md` states that every run pins its engine version, policy version
and platform. It did not — manifests carried the config and nothing about the
software that executed it. A benchmark result that cannot say which vLLM built
it cannot be reproduced or compared against a later one, which is the entire
premise.

Everything here is best-effort. A field that cannot be determined is recorded
as None rather than guessed, because a wrong version is worse than an absent
one: it makes a mismatch invisible.
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx

#: Packages worth recording. Engine behaviour and timing both depend on them,
#: and httpx is here because the client is where latency is measured.
TRACKED = ("admitperf", "httpx", "modal")


def package_versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in TRACKED:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def code_version() -> dict[str, Any]:
    """Which commit produced this result, and whether the tree was dirty.

    The package version is useless for this: it reads 0.0.1 for months while the
    code underneath changes daily. Without the commit, a bundle cannot be placed
    on either side of a fix, which is exactly the provenance a paper about
    provenance has to have.
    """
    import subprocess

    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() or None

    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return {"commit": None, "dirty": None}
    status = _git("status", "--porcelain")
    return {
        "commit": sha[:12],
        # A dirty tree means the commit does not describe what ran. Better to
        # say so in the record than to imply a clean provenance we do not have.
        "dirty": bool(status),
    }


def local_environment() -> dict[str, Any]:
    """The machine generating the load, which is where timings are taken."""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": package_versions(),
        "code": code_version(),
    }


async def engine_environment(
    base_url: str, *, timeout_s: float = 5.0, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """The serving engine's own version and resolved cache settings.

    vLLM publishes its version at /version, and its cache configuration as
    labels on `vllm:cache_config_info`. The latter is worth capturing because
    it reports what the engine *actually* did with the flags it was given —
    block size and prefix caching in particular are resolved at startup and can
    differ from what was requested.
    """
    info: dict[str, Any] = {"version": None, "cache_config": None}
    owned = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        try:
            resp = await http.get(f"{base_url}/version", timeout=timeout_s)
            if resp.status_code == 200:
                info["version"] = resp.json().get("version")
        except (httpx.HTTPError, ValueError):
            # Older builds and non-vLLM engines have no /version. Absent is the
            # honest answer; failing the run would not be.
            pass

        try:
            resp = await http.get(f"{base_url}/metrics", timeout=timeout_s)
            if resp.status_code == 200:
                info["cache_config"] = _cache_config(resp.text)
        except httpx.HTTPError:
            pass
    finally:
        if owned:
            await http.aclose()
    return info


def _cache_config(metrics_text: str) -> dict[str, str] | None:
    """Pull the label set off vllm:cache_config_info."""
    for line in metrics_text.splitlines():
        if not line.startswith("vllm:cache_config_info{"):
            continue
        labels = line[line.index("{") + 1 : line.rindex("}")]
        out: dict[str, str] = {}
        for part in labels.split(","):
            if "=" not in part:
                continue
            key, _, value = part.partition("=")
            out[key.strip()] = value.strip().strip('"')
        return out
    return None


__all__ = ["engine_environment", "local_environment", "package_versions"]
