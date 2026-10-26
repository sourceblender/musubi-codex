"""Codex protocol binding for shared bounded Musubi SessionStart continuity."""

from __future__ import annotations

import json
import os
import sys

from musubi_harness.plugin_continuity import MAX_CONTENT, MAX_ROWS, PluginContinuity
from musubi_harness.plugin_runtime import RuntimeConfigError
from musubi_harness.tokens import token_presence_problems

from .runtime import runtime

_continuity = PluginContinuity(runtime)


def continuity_block() -> str:
    if os.environ.get("MUSUBI_ACTOR"):
        try:
            config = runtime.runtime_config()
            connection = runtime._environment_connection()
            if connection is not None:
                problems = token_presence_problems(connection[1], config.presence)
                if problems:
                    return (
                        "## Musubi continuity\nMusubi seat token: "
                        + "; ".join(problems)
                        + ". Remote recall and delivery are refused."
                    )
        except RuntimeConfigError:
            pass  # The normal continuity renderer reports invalid configuration.
    return _continuity.continuity_block()


def main() -> int:
    try:
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        hook = json.loads(raw) if raw.strip() else {}
        if not isinstance(hook, dict):
            raise ValueError("hook_payload_invalid")
        with runtime.hook_context(hook):
            if runtime._project is not None:
                runtime.data_root()  # Publish the actual plugin-data locator before MCP writes.
            print(continuity_block())
    except (RuntimeConfigError, ValueError, OSError):
        print(
            "## Musubi continuity\nMusubi recent: unavailable (project_context_invalid). Do not interpret this as an empty memory set."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_CONTENT",
    "MAX_ROWS",
    "continuity_block",
    "main",
]
