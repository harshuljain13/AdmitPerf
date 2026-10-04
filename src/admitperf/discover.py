"""Find logs and group them by what they measured, not by where they sit."""

from __future__ import annotations

import json
from pathlib import Path

from admitperf.experiment import Experiment
from admitperf.policy_runs import PolicyRuns

#: Directories never worth walking.
SKIP = {".venv", "node_modules", ".git", "__pycache__", ".pytest_cache", ".ruff_cache"}

#: Fields identifying a line as something AdmitPerf wrote. Any directory may hold
#: unrelated .jsonl — request traces, dataset shards — and offering those as policies under test
#: would be worse than missing them.
OURS = ("signals", "verdict", "outcome", "scrape_error")


def _head(log: Path) -> dict | None:
    """The first record, or None if this is not one of ours.

    Only the first line, because identity is the same on every line and reading a
    200,000-line log to decide whether to offer it in a menu is not free.
    """
    try:
        with log.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                return rec if any(k in rec for k in OURS) else None
    except (OSError, ValueError):
        return None
    return None


def experiments(root: str | Path = ".") -> dict[str, Experiment]:
    """Every experiment under `root`, by name.

    Grouping comes from the `experiment` field a policy declared, never from the
    directory layout — otherwise moving a file changes what the result claims to be.

    A log with no experiment name falls back to its parent directory, so logs written
    before the field existed still appear. That fallback is labelled in the name, so a
    reader can see which grouping was declared and which was guessed.
    """
    root = Path(root)
    found: dict[str, Experiment] = {}

    for log in sorted(root.rglob("*.jsonl")):
        if SKIP & set(log.parts):
            continue
        head = _head(log)
        if head is None:
            continue

        name = head.get("experiment")
        if not name:
            parent = log.parent.relative_to(root)
            name = f"{parent if str(parent) != '.' else root.name} (unnamed)"

        policy = head.get("policy") or "no policy"
        exp = found.setdefault(name, Experiment(name=name))
        exp.notes = exp.notes or head.get("notes")
        runs = exp.policies.setdefault(
            policy,
            PolicyRuns(experiment=name, policy=policy, baseline=bool(head.get("baseline"))),
        )
        runs.logs.append(log)

    return found


def find(name: str, root: str | Path = ".") -> Experiment:
    """One experiment by name, with a message that lists the alternatives.

    "no such experiment" is useless on its own — the whole point of naming things is
    being able to ask for one by name and be told what the names are.
    """
    all_of_them = experiments(root)
    if name in all_of_them:
        return all_of_them[name]
    known = ", ".join(sorted(all_of_them)) or "none found"
    raise KeyError(f"no experiment named {name!r} under {root}. Found: {known}")
