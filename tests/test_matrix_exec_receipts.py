"""Blank finals recover only validated receipts from supported tool envelopes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from musubi_codex.stop import AdapterError, _receipt_candidates, _validated_receipt, build_envelopes


def receipt(
    route: str = "matrix-reply", status: str = "submitted", text: str = "Sent reply"
) -> dict:
    payload = {"text": text, "files": []}
    return {
        "schema_version": 1,
        "kind": "engawa.mutation_receipt",
        "logical_id": "engawa:synthetic",
        "transport_id": "$synthetic:example.test",
        "route": {"kind": route, "from": "yua", "to": "!synthetic:example.test"},
        "status": status,
        "payload": payload,
        "payload_sha256": hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def exec_output(text: str, *, exit_code: int = 0) -> str:
    return json.dumps(
        {"chunk_id": "synthetic", "wall_time_seconds": 0.1, "exit_code": exit_code, "output": text}
    )


@pytest.mark.parametrize("route", ["matrix-room", "matrix-reply"])
def test_matrix_authored_receipts_validate(route: str) -> None:
    assert _validated_receipt(receipt(route))["route"]["kind"] == route


@pytest.mark.parametrize("status", ["submitted-partial", "injected", "reaction-applied"])
def test_matrix_reply_wrong_or_partial_status_is_refused(status: str) -> None:
    with pytest.raises(AdapterError, match="bridge_receipt_status_invalid"):
        _validated_receipt(receipt(status=status))


def test_receipt_discovery_decodes_successful_exec_envelope() -> None:
    good = receipt()
    assert _receipt_candidates(exec_output(json.dumps(good))) == [good]


def test_receipt_discovery_does_not_traverse_arbitrary_json_or_failed_commands() -> None:
    good = json.dumps(receipt())
    assert not _receipt_candidates(json.dumps({"example": good}))
    assert not _receipt_candidates(json.dumps({"output": good}))
    assert not _receipt_candidates(exec_output(good, exit_code=1))


@pytest.mark.parametrize("reaction", [True, False])
def test_exec_wrapped_matrix_final_recovers_authored_text_or_skips_reaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reaction: bool
) -> None:
    for key, value in {
        "MUSUBI_ACTOR": "yua",
        "MUSUBI_PRESENCE": "yua/command-chair",
        "MUSUBI_ZONE": "home",
        "MUSUBI_DELIVERY_MODE": "shadow",
    }.items():
        monkeypatch.setenv(key, value)
    rows = [
        {"type": "turn_context", "payload": {"turn_id": "target"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "id": "user-1",
                "content": [{"type": "input_text", "text": "All saved."}],
                "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "output": [
                    {"type": "input_text", "text": "Script completed"},
                    {
                        "type": "input_text",
                        "text": exec_output(
                            json.dumps(
                                receipt("matrix-reaction", "reaction-applied", "")
                                if reaction
                                else receipt()
                            )
                        ),
                    },
                ],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "id": "answer-1",
                "content": [{"type": "output_text", "text": ""}],
            },
        },
    ]
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    declines: list[str] = []
    result = build_envelopes(
        {"session_id": "session", "turn_id": "target", "transcript_path": str(path)},
        declines=declines,
    )
    if reaction:
        assert result == []
    else:
        assert len(result) == 1
        assert result[0]["assistant_text"] == "Sent reply"
        assert result[0]["metadata"]["bridge_route"] == "matrix-reply"
    assert declines == []


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_nested_exec_receipts_keep_validation_and_order(depth: int) -> None:
    first = receipt()
    second = receipt()
    second["logical_id"] = "engawa:second"
    text = json.dumps(first) + "\n" + json.dumps(second)
    for _ in range(depth):
        text = exec_output(text)
    assert _receipt_candidates(text) == [first, second]


def test_receipt_discovery_bounds_exec_nesting() -> None:
    text = json.dumps(receipt())
    for _ in range(5):
        text = exec_output(text)
    assert not _receipt_candidates(text)


def test_exec_wrapping_does_not_bypass_payload_digest() -> None:
    bad = receipt()
    bad["payload"]["text"] = "Changed after signing"
    candidates = _receipt_candidates(exec_output(json.dumps(bad)))
    assert len(candidates) == 1
    with pytest.raises(AdapterError, match="bridge_receipt_digest_invalid"):
        _validated_receipt(candidates[0])
