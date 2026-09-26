"""Stage the native Codex user prompt for the matching Stop hook."""

from __future__ import annotations

import json
import sys

from musubi_harness import RuntimeConfigError

from .prompt_recall import context_for
from .prompt_stage import store_prompt


def main() -> int:
    try:
        hook = json.load(sys.stdin)
        if not isinstance(hook, dict):
            raise ValueError("hook_payload_invalid")
        store_prompt(hook)
        context = context_for(hook["prompt"])
    except (RuntimeConfigError, ValueError, OSError, json.JSONDecodeError):
        # Capture is advisory; Stop falls back to the existing transcript path.
        pass
    else:
        if context:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": context,
                        }
                    }
                )
            )
            return 0
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
