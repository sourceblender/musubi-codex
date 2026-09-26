"""Codex protocol binding for shared bounded Musubi SessionStart continuity."""

from __future__ import annotations

import os

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
    print(continuity_block())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_CONTENT",
    "MAX_ROWS",
    "continuity_block",
    "main",
]
