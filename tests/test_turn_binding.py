"""Exercise prompt acceptance, configuration edits, Stop, and recovery together."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from musubi_harness.plugin_runtime import RuntimeConfigError
from test_project_settings import project
from test_stop import _assistant_message, _turn_context, _user_message, _write_transcript

from musubi_codex import interrupt, stop, user_prompt_submit
from musubi_codex.pending import load_all
from musubi_codex.prompt_stage import read_prompt
from musubi_codex.runtime import runtime
from musubi_codex.turn_binding import read_binding


@pytest.mark.parametrize("change", ["identity", "policy", "removed", "malformed", "credentials"])
def test_native_turn_keeps_accepted_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    path = project(tmp_path)
    original = path.read_text()
    transcript = tmp_path / "turn.jsonl"
    hook = {
        "cwd": str(tmp_path),
        "session_id": "s",
        "turn_id": "t",
        "prompt": "question",
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook)))
    assert user_prompt_submit.main() == 0
    selected = read_binding(data, hook)
    assert selected is not None
    root = data / "projects" / selected.fingerprint / "yua/home"
    stage = next((root / "prompt-stage").glob("*.json"))
    _write_transcript(
        transcript, [_turn_context("t"), _user_message("question"), _assistant_message("answer")]
    )
    if change == "identity":
        project(tmp_path, "tama")
    elif change == "policy":
        project(tmp_path, delivery="verified")
    elif change == "removed":
        path.unlink()
    elif change == "malformed":
        path.write_text("[")
    else:
        selected.env_file.write_text("MUSUBI_API_URL=https://yua.invalid\nMUSUBI_TOKEN=rotated\n")
    delivered = []
    monkeypatch.setattr(
        stop, "_deliver", lambda rows, config, **_k: delivered.append((rows, config))
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook)))
    assert stop.main([]) == 0
    if change != "credentials":
        assert not list(data.rglob("shadow.db"))
        candidates = load_all(root)
        assert len(candidates) == 1
        assert candidates[0][1].project_fingerprint == selected.fingerprint
        assert stage.exists()
        assert not delivered
        # Interrupt must also preserve the original staged prompt after an edit.
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook)))
        assert interrupt.main() == 0
        assert stage.exists()
        path.write_text(original)
        # Startup recovery reuses the original candidate, identity, and policy.
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
        assert stop.main(["--drain-only"]) == 0
    databases = list(data.rglob("shadow.db"))
    assert databases == [root / "shadow.db"]
    with sqlite3.connect(databases[0]) as connection:
        assert connection.execute("select count(*) from capture_events").fetchone()[0] == 1
    rows, config = delivered[-1]
    assert config.actor == "yua" and config.delivery_mode == "shadow"
    assert rows[0]["actor"] == "yua"
    assert not load_all(root)
    assert not stage.exists()
    # Binding remains to fence a delayed duplicate Stop or prompt.
    assert read_binding(data, hook) == selected


def test_binding_is_immutable_private_and_contains_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path)
    hook = {"cwd": str(tmp_path), "session_id": "s", "turn_id": "t", "prompt": "question"}
    with runtime.hook_context(hook):
        from musubi_codex.prompt_stage import store_prompt

        store_prompt(hook)
        store_prompt(hook)
    binding = next((tmp_path / "data/turn-bindings").glob("*.json"))
    assert binding.stat().st_mode & 0o777 == 0o600
    assert "secret" not in binding.read_text() and "question" not in binding.read_text()
    project(tmp_path, "tama")
    with runtime.hook_context(hook), pytest.raises(RuntimeConfigError, match="rebind_refused"):
        store_prompt(hook)
    with runtime.hook_context(hook, honor_turn=True), pytest.raises(RuntimeConfigError):
        read_prompt(hook)


def test_unbound_project_stop_never_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"cwd": str(tmp_path), "session_id": "s", "turn_id": "t"})),
    )
    monkeypatch.setattr(stop, "_capture_current", lambda *_a: pytest.fail("unbound capture"))
    assert stop.main([]) == 0


def test_binding_symlink_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path)
    hook = {"cwd": str(tmp_path), "session_id": "s", "turn_id": "t"}
    with runtime.hook_context(hook):
        runtime.record_turn(hook)
    binding = next((tmp_path / "data/turn-bindings").glob("*.json"))
    target = tmp_path / "target"
    binding.rename(target)
    binding.symlink_to(target)
    with pytest.raises(RuntimeConfigError):
        read_binding(tmp_path / "data", hook)
