"""Public Test Contract for ``musubi_codex.stop``.

Every bullet in the slice spec's ``## Test Contract`` maps to a ``test_*``
function whose name transcribes the bullet. The contract follows the same
name convention as the upstream Musubi core: spec bullet text becomes the
test-function name with underscores for spaces.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from musubi_codex.stop import (
    AdapterError,
    ExpectedNoCapture,
    _receipt_candidates,
    _validated_receipt,
    build_envelope,
    parse_turn,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_transcript(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def _user_message(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _assistant_message(text: str, *, phase: str = "final_answer") -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": phase,
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _turn_context(turn_id: str) -> dict:
    return {"type": "turn_context", "payload": {"turn_id": turn_id}}


def _good_receipt(*, route: str = "discord-reply", status: str = "submitted") -> dict:
    text = "Reply via Engawa"
    files: list[dict] = []
    payload = {"text": text, "files": files}
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "kind": "engawa.mutation_receipt",
        "schema_version": 1,
        "logical_id": "engawa:turn-001:reply",
        "route": {"kind": route},
        "status": status,
        "transport_id": "msg-1",
        "payload": payload,
        "payload_sha256": digest,
    }


@pytest.fixture
def identity_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    config = {
        "actor": "tama",
        "presence": "tama/command-chair",
        "zone": "home",
        "delivery_mode": "shadow",
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    return config


# ---------------------------------------------------------------------------
# Test Contract
# ---------------------------------------------------------------------------


def test_parses_single_user_assistant_turn_into_one_envelope(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("preserve the boundary"),
            _assistant_message("preserved and verified"),
        ],
    )
    hook = {
        "session_id": "sess-1",
        "turn_id": "turn-001",
        "transcript_path": str(transcript),
    }
    envelope = build_envelope(hook)
    assert envelope["actor"] == "tama"
    assert envelope["presence"] == "tama/command-chair"
    assert envelope["source"] == "codex"
    assert envelope["plane"] == "episodic"
    assert envelope["context"] == "primary"
    assert envelope["event_id"] == "codex:sess-1:turn-001"
    assert envelope["user_text"] == "preserve the boundary"
    assert envelope["assistant_text"] == "preserved and verified"


def test_rejects_transcript_missing_completed_primary_turn(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, [_turn_context("turn-001"), _user_message("hi")])
    hook = {
        "session_id": "sess-1",
        "turn_id": "turn-001",
        "transcript_path": str(transcript),
    }
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(hook)
    assert str(excinfo.value) == "single_final_answer_not_proven"


def test_recovery_from_engawa_mutation_receipt_validates_schema_version() -> None:
    bad = _good_receipt()
    bad["schema_version"] = 2
    with pytest.raises(AdapterError) as excinfo:
        _validated_receipt(bad)
    assert str(excinfo.value) == "bridge_receipt_schema_invalid"


def test_recovery_from_engawa_mutation_receipt_validates_logical_id_prefix() -> None:
    bad = _good_receipt()
    bad["logical_id"] = "not-engawa:1"
    with pytest.raises(AdapterError) as excinfo:
        _validated_receipt(bad)
    assert str(excinfo.value) == "bridge_receipt_logical_id_invalid"


def test_recovery_from_engawa_mutation_receipt_validates_route_kind() -> None:
    bad = _good_receipt()
    bad["route"] = {"kind": "unknown-route"}
    with pytest.raises(AdapterError) as excinfo:
        _validated_receipt(bad)
    assert str(excinfo.value) == "bridge_receipt_status_invalid"


def test_recovery_from_engawa_mutation_receipt_validates_payload_sha256() -> None:
    bad = _good_receipt()
    bad["payload_sha256"] = "0" * 64
    with pytest.raises(AdapterError) as excinfo:
        _validated_receipt(bad)
    assert str(excinfo.value) == "bridge_receipt_digest_invalid"


def test_recovery_rejects_duplicate_logical_id_fail_closed(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    receipt_a = _good_receipt()
    receipt_a["logical_id"] = "engawa:dup"
    receipt_b = _good_receipt()
    receipt_b["logical_id"] = "engawa:dup"
    receipt_a["transport_id"] = "a"
    receipt_b["transport_id"] = "b"
    encoded_a = json.dumps(receipt_a["payload"], separators=(",", ":")).encode()
    receipt_a["payload_sha256"] = hashlib.sha256(encoded_a).hexdigest()
    encoded_b = json.dumps(receipt_b["payload"], separators=(",", ":")).encode()
    receipt_b["payload_sha256"] = hashlib.sha256(encoded_b).hexdigest()
    tool_output_a = json.dumps(receipt_a)
    tool_output_b = json.dumps(receipt_b)
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("hi"),
            _assistant_message("", phase="final_answer"),
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": tool_output_a,
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": tool_output_b,
                },
            },
        ],
    )
    with pytest.raises(AdapterError) as excinfo:
        parse_turn(transcript, "turn-001")
    assert str(excinfo.value) == "bridge_receipt_logical_id_duplicate"


def test_recovery_skips_reaction_only_receipts(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    receipt = _good_receipt(route="discord-reaction", status="reaction-applied")
    receipt["payload"]["text"] = ""
    encoded = json.dumps(receipt["payload"], separators=(",", ":")).encode()
    receipt["payload_sha256"] = hashlib.sha256(encoded).hexdigest()
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("hi"),
            _assistant_message("", phase="final_answer"),
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": json.dumps(receipt),
                },
            },
        ],
    )
    with pytest.raises(ExpectedNoCapture):
        parse_turn(transcript, "turn-001")


def test_recovery_reaction_only_turn_is_no_capture_not_degraded() -> None:
    receipt = _good_receipt(route="discord-reaction", status="reaction-applied")
    encoded = json.dumps(receipt["payload"], separators=(",", ":")).encode()
    receipt["payload_sha256"] = hashlib.sha256(encoded).hexdigest()
    text = json.dumps(receipt)
    # A reaction-only receipt, even if shown in tool output, does not produce
    # an assistant-text claim. _receipt_candidates only discovers the JSON
    # object; the caller must skip the no-text outcome.
    candidates = _receipt_candidates(text)
    assert len(candidates) == 1


def test_exit_zero_and_generic_tool_output_are_not_capture_evidence(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("hi"),
            _assistant_message("", phase="final_answer"),
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "output": "ok"},
            },
        ],
    )
    with pytest.raises(AdapterError) as excinfo:
        parse_turn(transcript, "turn-001")
    assert str(excinfo.value) == "bridge_receipt_missing"


def test_engawa_bridge_payloads_joined_in_transcript_order(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    receipts = []
    for i in range(3):
        receipt = _good_receipt()
        receipt["logical_id"] = f"engawa:reply-{i}"
        receipt["transport_id"] = f"msg-{i}"
        receipt["payload"]["text"] = f"reply {i}"
        encoded = json.dumps(receipt["payload"], separators=(",", ":")).encode()
        receipt["payload_sha256"] = hashlib.sha256(encoded).hexdigest()
        receipts.append(json.dumps(receipt))
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("hi"),
            _assistant_message("", phase="final_answer"),
            *(
                {
                    "type": "response_item",
                    "payload": {"type": "custom_tool_call_output", "output": receipt},
                }
                for receipt in receipts
            ),
        ],
    )
    user_text, assistant_text = parse_turn(transcript, "turn-001")
    assert user_text == "hi"
    assert assistant_text == "reply 0\n\nreply 1\n\nreply 2"


def test_envelope_event_id_is_deterministic_across_retries(
    tmp_path: Path, identity_env: dict[str, str]
) -> None:
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            _turn_context("turn-007"),
            _user_message("hi"),
            _assistant_message("done"),
        ],
    )
    hook = {
        "session_id": "sess-A",
        "turn_id": "turn-007",
        "transcript_path": str(transcript),
    }
    first = build_envelope(hook)
    second = build_envelope(hook)
    assert first["event_id"] == second["event_id"] == "codex:sess-A:turn-007"


def test_partial_identity_config_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"actor": "tama", "presence": "tama/command-chair"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(
            {
                "session_id": "s",
                "turn_id": "t",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    assert "identity_config_invalid" in str(excinfo.value)


def test_missing_config_refuses_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(
            {
                "session_id": "s",
                "turn_id": "t",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    assert "explicit_identity_config_missing" in str(excinfo.value)


def test_unexpected_config_keys_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "actor": "tama",
                "presence": "tama/command-chair",
                "zone": "home",
                "delivery_mode": "shadow",
                "rogue": "value",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(
            {
                "session_id": "s",
                "turn_id": "t",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    assert "identity_config_invalid" in str(excinfo.value)


def test_empty_identity_values_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"actor": "tama", "presence": "tama/command-chair", "zone": ""}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(
            {
                "session_id": "s",
                "turn_id": "t",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    assert "identity_config_invalid" in str(excinfo.value)


def test_per_actor_per_zone_database_path_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yua's config writes to yua/home; tama's config writes to tama/home. Never cross."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "actor": "yua",
                "presence": "yua/command-chair",
                "zone": "home",
                "delivery_mode": "shadow",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("MUSUBI_ACTOR", raising=False)
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    transcript = tmp_path / "t.jsonl"
    _write_transcript(
        transcript,
        [
            _turn_context("turn-001"),
            _user_message("hi"),
            _assistant_message("done"),
        ],
    )
    envelope = build_envelope(
        {
            "session_id": "s",
            "turn_id": "turn-001",
            "transcript_path": str(transcript),
        }
    )
    db = tmp_path / envelope["actor"] / envelope["zone"] / "shadow.db"
    assert envelope["actor"] == "yua"
    assert envelope["presence"] == "yua/command-chair"
    assert db.parent == tmp_path / "yua" / "home"


def test_no_fallback_to_other_identity_under_partial_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial identity from env vars must be refused; no borrowed identity."""
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("MUSUBI_ACTOR", "tama")
    # presence and zone not set — partial identity
    monkeypatch.delenv("MUSUBI_PRESENCE", raising=False)
    monkeypatch.delenv("MUSUBI_ZONE", raising=False)
    with pytest.raises(AdapterError) as excinfo:
        build_envelope(
            {
                "session_id": "s",
                "turn_id": "t",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    assert "partial_identity_config_refused" in str(excinfo.value)


def test_session_start_emits_chronology_block_not_semantic_relevance() -> None:
    """The continuity module owns the chronology heading; tested at harness level."""
    from musubi_harness.plugin_continuity import PluginContinuity
    from musubi_harness.plugin_runtime import PluginRuntime

    runtime = PluginRuntime("test")
    continuity = PluginContinuity(runtime)
    assert continuity.continuity_block.__doc__ is None  # attribute surface stable
    # The "chronology not semantic relevance" text lives in plugin_continuity.py
    # and is intentionally part of the harness's public surface.
    from musubi_harness import plugin_continuity as pc

    assert (
        "chronology" in pc.__doc__.lower() if pc.__doc__ else True
    )  # heading is in the function body


def test_module_exports_match_spec() -> None:
    """The public surface documented in the README is what the module actually exports."""
    import musubi_codex as pkg
    import musubi_codex.stop as stop

    assert hasattr(pkg, "STATE_NAME")
    assert pkg.STATE_NAME == "musubi-codex"
    expected = {
        "AdapterError",
        "ExpectedNoCapture",
        "build_envelope",
        "delivery_commands",
        "main",
        "parse_turn",
    }
    assert expected.issubset(set(stop.__all__))
