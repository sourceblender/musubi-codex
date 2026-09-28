"""Transcript message IDs, not Codex turn IDs, delimit capture exchanges."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from musubi_codex.stop import build_envelopes
from musubi_codex.stop import main as stop_main


@pytest.fixture(autouse=True)
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "actor": "yua",
                "presence": "yua/command-chair",
                "zone": "home",
                "delivery_mode": "shadow",
            }
        )
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    for key in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(key, raising=False)


def context(turn: str) -> dict:
    return {"type": "turn_context", "payload": {"turn_id": turn}}


def user(identity: str, text: str, *, kind: str = "user.text") -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "id": identity,
            "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": {"content_item_kinds": [kind]},
        },
    }


def answer(identity: str, text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "id": identity,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def envelopes(
    tmp_path: Path, rows: list[dict], turn: str = "target", declines: list[str] | None = None
) -> list[dict]:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return build_envelopes(
        {"session_id": "session", "turn_id": turn, "transcript_path": str(path)},
        declines=declines,
    )


def test_two_finals_under_one_turn_have_distinct_identity_and_spans(tmp_path: Path) -> None:
    result = envelopes(
        tmp_path,
        [
            context("target"),
            user("msg-u1", "First"),
            answer("msg-a1", "One"),
            user("msg-u2", "Second"),
            user("msg-u3", "Steer"),
            answer("msg-a2", "Two"),
        ],
    )
    assert [item["event_id"] for item in result] == [
        "exchange.v1:codex:session:msg-a1",
        "exchange.v1:codex:session:msg-a2",
    ]
    assert [item["user_text"] for item in result] == ["First", "Second\n\nSteer"]
    assert json.loads(result[1]["metadata"]["input_record_ids"]) == ["msg-u2", "msg-u3"]


def test_interrupted_input_folds_into_later_answer_and_machine_context_does_not(
    tmp_path: Path,
) -> None:
    result = envelopes(
        tmp_path,
        [
            context("aborted"),
            user("msg-u1", "Start"),
            {"type": "event_msg", "payload": {"type": "turn_aborted", "turn_id": "aborted"}},
            context("target"),
            user("msg-meta", "host config", kind="environments.environment_context"),
            user("msg-u2", "Continue"),
            answer("msg-a1", "Done"),
        ],
    )
    assert len(result) == 1
    assert result[0]["user_text"] == "Start\n\nContinue"
    assert json.loads(result[0]["metadata"]["input_record_ids"]) == ["msg-u1", "msg-u2"]


def test_duplicate_serialization_of_one_host_message_is_not_a_new_input(tmp_path: Path) -> None:
    first = user("msg-u1", "Prompt")
    result = envelopes(tmp_path, [context("target"), first, first, answer("msg-a1", "Reply")])
    assert result[0]["user_text"] == "Prompt"


def test_unknown_input_kind_declines_instead_of_guessing(tmp_path: Path) -> None:
    declined: list[str] = []
    assert not envelopes(
        tmp_path,
        [
            context("target"),
            user("msg-u1", "unknown", kind="future.kind"),
            answer("msg-a1", "Reply"),
        ],
        declines=declined,
    )
    assert declined == ["input_provenance_unknown"]


def test_many_input_ids_use_digest_and_count(tmp_path: Path) -> None:
    rows = [context("target")]
    rows.extend(user(f"msg-u-{i:04d}-" + "x" * 32, f"Input {i}") for i in range(40))
    rows.append(answer("msg-a1", "Reply"))
    result = envelopes(tmp_path, rows)[0]
    assert result["metadata"]["input_record_count"] == "40"
    assert len(result["metadata"]["input_record_ids_sha256"]) == 64
    assert "input_record_ids" not in result["metadata"]


def test_per_record_text_digest_detects_same_joined_display_text(tmp_path: Path) -> None:
    first = envelopes(
        tmp_path,
        [
            context("target"),
            user("msg-u1", "a\n\nb"),
            user("msg-u2", "c"),
            answer("msg-a1", "Reply"),
        ],
    )[0]
    second = envelopes(
        tmp_path,
        [
            context("target"),
            user("msg-u1", "a"),
            user("msg-u2", "b\n\nc"),
            answer("msg-a1", "Reply"),
        ],
    )[0]
    assert first["user_text"] == second["user_text"]
    assert (
        first["metadata"]["input_record_texts_sha256"]
        != second["metadata"]["input_record_texts_sha256"]
    )


def test_missing_answer_id_declines(tmp_path: Path) -> None:
    final = answer("msg-a1", "Reply")
    del final["payload"]["id"]
    declined: list[str] = []
    assert not envelopes(
        tmp_path, [context("target"), user("msg-u1", "Prompt"), final], declines=declined
    )
    assert declined == ["host_message_id_missing"]


def test_context_only_final_declines_without_dropping_later_valid_final(tmp_path: Path) -> None:
    rows = [
        context("target"),
        user("msg-c1", "<env>", kind="environments.environment_context"),
        answer("msg-a1", "One"),
        user("msg-u2", "Real question"),
        answer("msg-a2", "Two"),
    ]
    result = envelopes(tmp_path, rows)
    assert [item["event_id"] for item in result] == ["exchange.v1:codex:session:msg-a2"]


def test_bad_provenance_in_earlier_turn_does_not_poison_next(tmp_path: Path) -> None:
    old = user("msg-old", "older Codex record")
    del old["payload"]["internal_chat_message_metadata_passthrough"]
    rows = [
        context("earlier"),
        old,
        answer("msg-a0", "Old answer"),
        context("target"),
        user("msg-u1", "Now"),
        answer("msg-a1", "One"),
    ]
    result = envelopes(tmp_path, rows)
    assert [item["event_id"] for item in result] == ["exchange.v1:codex:session:msg-a1"]


def test_stop_enqueues_both_finals_under_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "transcript.jsonl"
    rows = [
        context("target"),
        user("msg-u1", "First"),
        answer("msg-a1", "One"),
        user("msg-u2", "Second"),
        answer("msg-a2", "Two"),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    seen: list[str] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(json.loads(str(kwargs["input"]))["event_id"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("musubi_codex.stop.subprocess.run", fake_run)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "session_id": "session",
                    "turn_id": "target",
                    "transcript_path": str(path),
                }
            )
        ),
    )
    assert stop_main() == 0
    assert capsys.readouterr().out == "{}\n"
    assert seen == ["exchange.v1:codex:session:msg-a1", "exchange.v1:codex:session:msg-a2"]
