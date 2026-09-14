"""The session file — what `infra up` leaves behind for `run` to find.

Bringing a model up takes minutes, and you will run several policies against
one deployment. So provisioning and running are separate commands, and this
file is how they talk: `up` writes it, `run` and `down` read it.

It also ends up in the results bundle, because "which engine, which model,
which GPU" is part of what a number means.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SESSION_DIR = Path(".admitperf")
SESSION_FILE = SESSION_DIR / "session.json"


@dataclass
class Session:
    """A provisioned engine deployment."""

    provider: str  # "modal" | "lambda" | "local"
    engine: str  # "vllm" | "sglang"
    model: str
    endpoints: list[str]
    gpu: str | None = None
    served_model_name: str = "lab"
    created_at: str = ""
    #: Anything the provider needs to tear itself down again — a Modal app
    #: name, a Lambda host, a pid file. Opaque to everyone else.
    handle: dict[str, str] = field(default_factory=dict)

    @property
    def primary(self) -> str:
        if not self.endpoints:
            raise ValueError("session has no endpoints")
        return self.endpoints[0]


class SessionStore:
    """Reads and writes the session file."""

    def __init__(self, path: Path = SESSION_FILE) -> None:
        self.path = path

    def save(self, session: Session) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(session), indent=2) + "\n")
        return self.path

    def load(self) -> Session:
        if not self.path.exists():
            raise FileNotFoundError(
                f"no session at {self.path}. Run `admitperf infra up` first, "
                "or pass --engine-url to point at an engine you started yourself."
            )
        return Session(**json.loads(self.path.read_text()))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def exists(self) -> bool:
        return self.path.exists()


__all__ = ["SESSION_FILE", "Session", "SessionStore"]
