"""Fence a prompt whose Codex turn was interrupted before completion."""

from __future__ import annotations

import json
import sys

from musubi_harness import RuntimeConfigError

from .prompt_stage import clear_prompt


def main() -> int:
    try:
        hook = json.load(sys.stdin)
        if not isinstance(hook, dict):
            raise ValueError("hook_payload_invalid")
        clear_prompt(hook)
    except (RuntimeConfigError, ValueError, OSError, json.JSONDecodeError):
        pass
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
