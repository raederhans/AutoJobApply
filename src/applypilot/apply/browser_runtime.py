"""Locate the supervised Host artifacts without reading a user workspace.

Installed wheels carry the same reviewed modules as a source checkout. Resolving
their location neither starts a browser nor establishes a runtime capability.
"""
from __future__ import annotations

from pathlib import Path

BROWSER_RUNTIME_FILES = (
    "visual-bridge-host.mjs",
    "attended-host-journal.mjs",
    "browser-form-state.mjs",
    "browser-form-semantics.mjs",
    "browser-field-batch.mjs",
    "browser-preparation-subgoals.mjs",
    "browser-observation-feedback.mjs",
)


def browser_host_info() -> dict[str, object]:
    """Prefer installed artifacts; never select code from the process cwd."""
    package = Path(__file__).resolve().parents[1]
    bundled = package / "browser_runtime"
    checkout = package.parent.parent
    if bundled.is_dir():
        directory, origin = bundled, "installed_package"
    elif package == checkout / "src" / "applypilot" and (checkout / "pyproject.toml").is_file():
        directory, origin = checkout / "scripts", "source_checkout"
    else:
        raise FileNotFoundError("This installation has no packaged Browser Host runtime.")
    missing = [name for name in BROWSER_RUNTIME_FILES if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError("Browser Host runtime is incomplete: " + ", ".join(missing))
    entry = directory / "visual-bridge-host.mjs"
    return {"entrypoint": str(entry), "module_uri": entry.as_uri(), "origin": origin,
            "modules": list(BROWSER_RUNTIME_FILES)}
