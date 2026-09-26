"""Private, turn-scoped prompt handoff between native Codex lifecycle hooks."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .runtime import data_root, runtime_config


def _identity(hook: dict[str, object]) -> tuple[str, str, str]:
    session_id = hook.get("session_id")
    turn_id = hook.get("turn_id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("hook_identity_missing")
    if not isinstance(turn_id, str) or not turn_id:
        raise ValueError("hook_identity_missing")
    configured = runtime_config()
    return session_id, turn_id, f"{configured.actor}/{configured.zone}"


def _path(hook: dict[str, object]) -> Path:
    session_id, turn_id, scope = _identity(hook)
    digest = hashlib.sha256(
        json.dumps([session_id, turn_id], separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return data_root() / scope / "prompt-stage" / f"{digest}.json"


def store_prompt(hook: dict[str, object]) -> None:
    prompt = hook.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt_missing")
    path = _path(hook)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    payload = {
        "session_id": hook["session_id"],
        "turn_id": hook["turn_id"],
        "prompt": prompt,
    }
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".prompt-", delete=False
        ) as handle:
            temporary = handle.name
            os.chmod(handle.name, 0o600)
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def read_prompt(hook: dict[str, object]) -> str | None:
    path = _path(hook)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("prompt_stage_invalid")
    if payload.get("session_id") != hook.get("session_id") or payload.get("turn_id") != hook.get(
        "turn_id"
    ):
        raise ValueError("prompt_stage_identity_mismatch")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt_stage_invalid")
    return prompt


def clear_prompt(hook: dict[str, object]) -> None:
    _path(hook).unlink(missing_ok=True)
