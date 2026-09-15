"""Policy registry — built-ins plus third-party discovery (spec D3).

Before this, `POLICIES` was a dict literal inside the package. Anyone who
installed the wheel could not add to it without editing installed source, which
made the package closed to precisely the extension it exists to enable.

A third-party policy now registers itself from its own distribution:

    # their pyproject.toml
    [project.entry-points."admitperf.policies"]
    my_policy = "my_pkg.policies:MyPolicy"

Resolution order is built-ins first, then entry points. A name collision is an
error naming both sources — never a silent override, because a silently
shadowed policy would invalidate every number in a run.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from admitperf.core.api import AdmissionPolicy

#: The published plugin group. Renaming the baseline package must never
#: change this — it is the public contract third-party packages declare
#: against, and a silent change makes every installed policy vanish.
ENTRY_POINT_GROUP = "admitperf.policies"

# Built-in policies, populated by admitperf.policies at import time to avoid a
# circular import (core must not depend on the policies subpackage).
_BUILTINS: dict[str, type[AdmissionPolicy]] = {}
_discovered: dict[str, type[AdmissionPolicy]] | None = None


def register(name: str, cls: type[AdmissionPolicy]) -> None:
    """Register a built-in policy. Idempotent for the same class."""
    existing = _BUILTINS.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"policy name {name!r} already registered to {existing.__module__}."
            f"{existing.__qualname__}"
        )
    _BUILTINS[name] = cls


def _discover() -> dict[str, type[AdmissionPolicy]]:
    """Load third-party policies from the entry-point group.

    A plugin that fails to import is a hard error: silently dropping it would
    mean `admitperf bench run --policy theirs` fails with "unknown policy", pointing
    the user at the wrong problem entirely.
    """
    global _discovered
    if _discovered is not None:
        return _discovered

    found: dict[str, type[AdmissionPolicy]] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            cls = ep.load()
        except Exception as exc:  # noqa: BLE001 - re-raised with context below
            raise ImportError(
                f"failed to load policy plugin {ep.name!r} from {ep.value!r}: {exc}"
            ) from exc

        if not (isinstance(cls, type) and issubclass(cls, AdmissionPolicy)):
            raise TypeError(
                f"entry point {ep.name!r} ({ep.value!r}) is not an AdmissionPolicy subclass"
            )

        name = getattr(cls, "name", ep.name)
        _ensure_builtins()
        if name in _BUILTINS:
            raise ValueError(
                f"policy plugin {ep.value!r} claims name {name!r}, which is a built-in; "
                "rename the plugin's `name` attribute"
            )
        if name in found:
            raise ValueError(f"two policy plugins both claim the name {name!r}")
        found[name] = cls

    _discovered = found
    return found


def _ensure_builtins() -> None:
    """Import the built-in policy package so it can self-register.

    Deferred to call time rather than module scope: `admitperf.policies`
    imports from `core`, so a top-level import here would be circular. This is
    not a layering violation — `baseline` is part of the light decision path,
    unlike bench/engines/runtime.
    """
    if not _BUILTINS:
        import admitperf.policies  # noqa: F401  (import registers them)


def available() -> dict[str, type[AdmissionPolicy]]:
    """Every resolvable policy: built-ins plus discovered plugins."""
    _ensure_builtins()
    return {**_BUILTINS, **_discover()}


def get_policy(name: str, **kwargs: object) -> AdmissionPolicy:
    """Resolve a policy by name and instantiate it."""
    registry = available()
    if name not in registry:
        raise KeyError(f"unknown policy {name!r}; registered: {sorted(registry)}")
    cls = registry[name]
    try:
        return cls(**kwargs)
    except TypeError as exc:
        # A bare TypeError from a dataclass constructor names neither the
        # policy nor the accepted parameters, and the usual cause is a config
        # file written against an older version of a policy.
        import inspect

        accepted = [p for p in inspect.signature(cls.__init__).parameters if p != "self"]
        raise TypeError(
            f"policy {name!r} rejected these settings: {exc}. It accepts: {accepted or '(none)'}"
        ) from exc


def requirements_of(policy: AdmissionPolicy) -> frozenset[str]:
    """Signals this policy needs from SystemState (spec D8).

    Policies that declare nothing are assumed to need nothing, which is true of
    the NoAdmission baseline and of any policy deciding purely on the request.
    """
    return frozenset(getattr(policy, "requires", frozenset()))


def reset_discovery_cache() -> None:
    """Test hook — forces re-scan of entry points."""
    global _discovered
    _discovered = None


__all__ = [
    "ENTRY_POINT_GROUP",
    "available",
    "get_policy",
    "register",
    "requirements_of",
    "reset_discovery_cache",
]
