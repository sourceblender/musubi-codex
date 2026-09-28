"""Native hooks stage a hint; the transcript proves the exchange identity."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from musubi_codex.interrupt import main as interrupt_main
from musubi_codex.prompt_stage import clear_prompt, read_prompt, store_prompt
from musubi_codex.stop import AdapterError, build_envelope
from musubi_codex.stop import main as stop_main


def transcript(path: Path, *, prompt: str = "Do the task", answer: str = "done") -> Path:
    records = [
        {"type": "turn_context", "payload": {"turn_id": "t"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "id": "msg-user-1",
                "content": [{"type": "input_text", "text": prompt}],
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "id": "msg-answer-1",
                "content": [{"type": "output_text", "text": answer}],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "actor": "tama",
                "presence": "tama/command-chair",
                "zone": "home",
                "delivery_mode": "shadow",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    for key in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path


def test_native_pair_requires_transcript_anchor(configured: Path) -> None:
    submitted = {"session_id": "s", "turn_id": "t", "prompt": "Fix the bug"}
    store_prompt(submitted)
    hook = {"session_id": "s", "turn_id": "t", "last_assistant_message": "Fixed it"}
    with pytest.raises(AdapterError, match="transcript_unavailable"):
        build_envelope(hook)
    hook["transcript_path"] = str(
        transcript(configured / "t.jsonl", prompt="Fix the bug", answer="Fixed it")
    )
    first = build_envelope(hook)
    second = build_envelope(hook)
    assert first["event_id"] == second["event_id"] == "exchange.v1:codex:s:msg-answer-1"
    assert first["user_text"] == "Fix the bug"
    assert first["assistant_text"] == "Fixed it"
    assert first["metadata"]["capture_source"] == "transcript_exchange"
    staged = list((configured / "tama" / "home" / "prompt-stage").glob("*.json"))
    assert len(staged) == 1
    assert staged[0].stat().st_mode & 0o777 == 0o600
    assert staged[0].parent.stat().st_mode & 0o777 == 0o700
    assert os.access(staged[0], os.R_OK)


def test_wrong_turn_cannot_borrow_staged_prompt(configured: Path) -> None:
    store_prompt({"session_id": "s", "turn_id": "first", "prompt": "private prompt"})
    with pytest.raises(AdapterError, match="transcript_unavailable"):
        build_envelope({"session_id": "s", "turn_id": "second", "last_assistant_message": "answer"})


def test_missing_final_never_uses_staged_prompt_alone(configured: Path) -> None:
    hook = {"session_id": "s", "turn_id": "t", "prompt": "Do the task"}
    store_prompt(hook)
    with pytest.raises(AdapterError, match="transcript_unavailable"):
        build_envelope({"session_id": "s", "turn_id": "t", "last_assistant_message": None})
    assert read_prompt(hook) == "Do the task"
    clear_prompt(hook)
    assert read_prompt(hook) is None


def test_stage_rejects_partial_identity(configured: Path) -> None:
    with pytest.raises(ValueError, match="hook_identity_missing"):
        store_prompt({"session_id": "s", "prompt": "Do the task"})
    assert not list(configured.rglob("prompt-stage"))


def test_interrupt_discards_prompt_without_capture(
    configured: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hook = {"session_id": "s", "turn_id": "t", "prompt": "Do the task"}
    store_prompt(hook)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s", "turn_id": "t"})))
    assert interrupt_main() == 0
    assert capsys.readouterr().out == "{}\n"
    assert read_prompt(hook) is None
    with pytest.raises(AdapterError, match="transcript_unavailable"):
        build_envelope({"session_id": "s", "turn_id": "t", "last_assistant_message": "answer"})


def test_stop_clears_prompt_only_after_successful_shadow_enqueue(
    configured: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    submitted = {"session_id": "s", "turn_id": "t", "prompt": "Do the task"}
    stopped = {
        "session_id": "s",
        "turn_id": "t",
        "last_assistant_message": "done",
        "transcript_path": str(transcript(configured / "t.jsonl")),
    }
    store_prompt(submitted)
    seen: list[dict[str, object]] = []

    def enqueue(_argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(json.loads(str(kwargs["input"])))
        return subprocess.CompletedProcess(_argv, 0, "", "")

    monkeypatch.setattr("musubi_codex.stop.subprocess.run", enqueue)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stopped)))
    assert stop_main() == 0
    assert capsys.readouterr().out == "{}\n"
    assert seen[0]["event_id"] == "exchange.v1:codex:s:msg-answer-1"
    assert read_prompt(submitted) is None


def test_failed_enqueue_keeps_pending_candidate_for_retry(
    configured: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    submitted = {"session_id": "s", "turn_id": "t", "prompt": "Do the task"}
    stopped = {
        "session_id": "s",
        "turn_id": "t",
        "last_assistant_message": "done",
        "transcript_path": str(transcript(configured / "t.jsonl")),
    }
    store_prompt(submitted)
    monkeypatch.setattr(
        "musubi_codex.stop.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "failed"),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stopped)))
    assert stop_main() == 0
    assert capsys.readouterr().out == "{}\n"
    assert read_prompt(submitted) is None
    pending = list((configured / "tama" / "home" / "pending-exchange").glob("*.json"))
    assert len(pending) == 1


def test_null_final_stop_clears_terminal_prompt(
    configured: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    submitted = {"session_id": "s", "turn_id": "t", "prompt": "Do the task"}
    store_prompt(submitted)
    stopped = {"session_id": "s", "turn_id": "t", "last_assistant_message": None}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stopped)))
    assert stop_main() == 0
    assert capsys.readouterr().out == "{}\n"
    assert read_prompt(submitted) is None
    degraded = (configured / "degraded.jsonl").read_text(encoding="utf-8")
    assert '"reason": "transcript_unavailable"' in degraded
