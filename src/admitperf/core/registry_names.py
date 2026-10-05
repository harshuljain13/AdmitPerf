"""Build a shipped policy by the name you type.

Separate from the policies themselves so the CLI does not import every one of them to
resolve a single name, and so a typo is answered with the list of valid names rather
than a traceback.
"""

from __future__ import annotations

from typing import Any

from admitperf.core.policy import Policy

#: name -> (class path, defaults). Defaults exist so `replay trace kv_threshold` works
#: without parameters; a policy with a required threshold and no default would make the
#: shortest useful command an error.
SHIPPED: dict[str, tuple[str, dict[str, Any]]] = {
    "no_admission": ("NoAdmission", {}),
    "kv_threshold": ("KvThreshold", {"threshold": 0.90}),
    "queue_depth": ("QueueDepth", {"max_waiting": 32}),
    "dual_gate": ("DualGate", {"threshold": 0.90, "min_hit_rate": 0.30}),
}


def build(name: str, **params: Any) -> Policy:
    if name not in SHIPPED:
        raise SystemExit(
            f"unknown policy {name!r}. Shipped: {', '.join(sorted(SHIPPED))}. "
            "`admitperf policies` describes each one."
        )
    cls_name, defaults = SHIPPED[name]
    from admitperf import policies as shipped

    return getattr(shipped, cls_name)(**{**defaults, **params})
