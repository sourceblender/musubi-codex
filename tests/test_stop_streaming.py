"""The transcript may be large; old identities and unfinished inputs still count."""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import pytest

from musubi_codex import stop


def context(turn: str) -> dict:
    return {"type": "turn_context", "payload": {"turn_id": turn}}


def message(role: str, identity: str, text: str) -> dict:
    payload = {
        "type": "message",
        "role": role,
        "id": identity,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }
    if role == "user":
        payload["internal_chat_message_metadata_passthrough"] = {
            "content_item_kinds": ["user.text"]
        }
    else:
        payload["phase"] = "final_answer"
    return {"type": "response_item", "payload": payload}


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_both_readers_stream_without_path_read_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(
        tmp_path / "t.jsonl",
        [context("target"), message("user", "u", "Ask"), message("assistant", "a", "Answer")],
    )
    original = Path.read_text

    def no_transcript_read(self: Path, *args: object, **kwargs: object) -> str:
        assert self != path, "must not materialize the whole transcript"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_transcript_read)
    assert stop.parse_turn(path, "target") == ("Ask", "Answer")
    assert stop._exchange_records(path, "target")[0]["inputs"] == [("u", "Ask")]


@pytest.mark.parametrize("collision", [False, True])
def test_earlier_message_ids_still_control_duplicates_and_collisions(
    tmp_path: Path, collision: bool
) -> None:
    path = write(
        tmp_path / "t.jsonl",
        [
            context("old"),
            message("user", "u-old", "Old"),
            message("assistant", "a-old", "Old answer"),
            context("target"),
            message("user", "u-target", "Do not lose me"),
            message("assistant", "a-old", "Different" if collision else "Old answer"),
            message("assistant", "a-target", "Done"),
        ],
    )
    records = stop._exchange_records(path, "target")
    if collision:
        assert records[0] == {"decline_reason": "host_message_id_collision"}
    else:
        assert records[0]["inputs"] == [("u-target", "Do not lose me")]
        assert records[0]["answer_id"] == "a-target"


def test_interrupted_input_from_an_earlier_turn_is_retained(tmp_path: Path) -> None:
    path = write(
        tmp_path / "t.jsonl",
        [
            context("interrupted"),
            message("user", "u-old", "Start"),
            context("target"),
            message("user", "u-new", "Continue"),
            message("assistant", "a", "Done"),
        ],
    )
    assert stop._exchange_records(path, "target")[0]["inputs"] == [
        ("u-old", "Start"),
        ("u-new", "Continue"),
    ]


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\u2028", "\u0085"])
def test_stream_keeps_read_text_splitlines_semantics(tmp_path: Path, separator: str) -> None:
    path = tmp_path / "t.jsonl"
    text = (
        separator.join(
            [
                json.dumps(context("target")),
                "",
                json.dumps(message("user", "u", "Ask")),
                json.dumps(message("assistant", "a", "Done")),
            ]
        )
        + separator
    )
    path.write_text(text)
    assert list(stop._transcript_lines(path)) == path.read_text().splitlines()


def test_missing_file_retains_the_named_reason(tmp_path: Path) -> None:
    with pytest.raises(stop.AdapterError, match="transcript_unreadable"):
        stop._exchange_records(tmp_path / "missing", "target")


def test_old_text_does_not_accumulate_in_memory(tmp_path: Path) -> None:
    path = tmp_path / "large.jsonl"
    with path.open("w") as handle:
        for i in range(600):
            for record in [
                context(str(i)),
                message("user", f"u-{i}", "x" * 24000),
                message("assistant", f"a-{i}", "y" * 24000),
            ]:
                handle.write(json.dumps(record) + "\n")
        for record in [
            context("target"),
            message("user", "u-last", "Ask"),
            message("assistant", "a-last", "Done"),
        ]:
            handle.write(json.dumps(record) + "\n")
    assert path.stat().st_size > 25_000_000
    tracemalloc.start()
    try:
        result = stop._exchange_records(path, "target")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result[0]["inputs"] == [("u-last", "Ask")]
    assert peak < 3_000_000, peak


def test_signature_accepts_json_lone_surrogates() -> None:
    assert stop._message_signature("user", "\ud800") != stop._message_signature("user", "\ud801")
