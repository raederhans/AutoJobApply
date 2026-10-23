"""Fail-closed filesystem namespaces for one application runtime turn.

The public package, CLI, and environment names intentionally remain
``applypilot``.  These paths are an internal isolation boundary so concurrent
pipeline runs and Agent turns never reuse transient MCP, report, or log files.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# CreateProcess rejects a working directory longer than MAX_PATH - 2 (258)
# characters with WinError 267, even with long paths enabled or an
# extended-length path prefix.  Agent processes run in ``output_root`` and in
# one nested per-turn directory (for example ``.app-server-shadow``), so reserve room for it.
_ENFORCE_PROCESS_CWD_LIMIT = os.name == "nt"
WINDOWS_PROCESS_CWD_LIMIT = 258
PROCESS_CWD_CHILD_RESERVE = 24


def _component(value: object, *, name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} is required")
    # Agent processes use these directories as their cwd, which Windows caps
    # at MAX_PATH.  Full identities remain available in ``as_dict``; disk
    # components are only a stable, collision-resistant digest.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def _contained(path: Path, root: Path) -> Path:
    resolved_root = root.expanduser().resolve(strict=False)
    resolved = path.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("runtime output escaped its namespace root") from exc
    return resolved


@dataclass(frozen=True, slots=True)
class RuntimeNamespace:
    """Bind a run, Agent session, browser profile, and output directory."""

    root: Path
    run_id: str
    session_id: str
    profile_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve(strict=False))
        for name in ("run_id", "session_id", "profile_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            object.__setattr__(self, name, value)

    @property
    def run_root(self) -> Path:
        return _contained(
            self.root / "runs" / _component(self.run_id, name="run"),
            self.root,
        )

    @property
    def session_root(self) -> Path:
        return _contained(
            self.run_root / _component(self.session_id, name="session"),
            self.run_root,
        )

    @property
    def output_root(self) -> Path:
        output_root = _contained(
            self.session_root / _component(self.profile_id, name="profile"),
            self.session_root,
        )
        required = len(str(output_root)) + PROCESS_CWD_CHILD_RESERVE
        if _ENFORCE_PROCESS_CWD_LIMIT and required > WINDOWS_PROCESS_CWD_LIMIT:
            raise ValueError(
                "runtime output directory is too long for Windows to start an "
                f"Agent process there ({len(str(output_root))} characters plus "
                f"{PROCESS_CWD_CHILD_RESERVE} reserved, limit "
                f"{WINDOWS_PROCESS_CWD_LIMIT}): {output_root}. Move the "
                "ApplyPilot workspace (APPLYPILOT_DIR or --workspace) to a "
                "shorter workspace path."
            )
        return output_root

    def path(self, name: str) -> Path:
        """Return one direct child path, rejecting traversal and aliases."""
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("runtime output name must be one direct child")
        return _contained(self.output_root / name, self.output_root)

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": "1",
            "run_id": self.run_id,
            "session_id": self.session_id,
            "profile_id": self.profile_id,
            "output_root": str(self.output_root),
        }
