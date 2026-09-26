"""Codex protocol binding for shared bounded Musubi SessionStart continuity."""

from __future__ import annotations

from musubi_harness.plugin_continuity import MAX_CONTENT, MAX_ROWS, PluginContinuity

from .runtime import runtime

_continuity = PluginContinuity(runtime)
continuity_block = _continuity.continuity_block


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
