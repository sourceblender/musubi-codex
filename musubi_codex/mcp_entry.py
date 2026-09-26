"""Delay MCP startup briefly until Codex publishes this install's hook data.

Codex can start the MCP executable before SessionStart. In the legacy plugin
layout, the hook publishes an owner-only locator under CODEX_HOME, while the
MCP child gets the installed plugin root as its working directory. Waiting
before importing the MCP server leaves Codex free to run SessionStart.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

STARTUP_WAIT_SECONDS = 3.0


def _locator_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    root = Path.cwd().resolve()
    digest = hashlib.sha256(os.fsencode(root)).hexdigest()
    return home / "plugins" / "data" / ".musubi-codex-locators" / f"{digest}.json"


def wait_for_hook() -> None:
    """Bound startup delay; the MCP server validates the locator afterward."""
    try:
        locator = _locator_path()
    except (OSError, RuntimeError):
        return
    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while not locator.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def main() -> int:
    wait_for_hook()
    from .mcp import main as serve_main

    return serve_main()
