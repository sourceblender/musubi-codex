"""Codex Stop adapter: parse one completed primary turn and shadow-enqueue it.

This module owns all Codex-transcript-specific parsing logic. The harness
package (``musubi-harness``) provides the envelope contract, the shadow
outbox, and the verified-delivery state machine. The adapter only
projects the Codex JSONL event stream onto that contract.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musubi_harness import RuntimeConfig, RuntimeConfigError

from .prompt_stage import clear_prompt, read_prompt
from .runtime import (
    data_root,
    harness_bin,
    memory_data_bin,
    plugin_config,
    runtime_config,
    tool_environment,
)


class AdapterError(ValueError):
    """The unstable Codex transcript could not be safely projected."""


class ExpectedNoCapture(Exception):
    """A proven bridge mutation intentionally carried no authored text."""


# ---------------------------------------------------------------------------
# Transcript record extraction
# ---------------------------------------------------------------------------


def _text_parts(
    message: dict[str, Any], expected_type: str, *, allow_blank: bool = False
) -> list[str]:
    content = message.get("content")
    if not isinstance(content, list):
        raise AdapterError("message_content_invalid")
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != expected_type:
            continue
        text = item.get("text")
        if not isinstance(text, str) or (not allow_blank and not text.strip()):
            raise AdapterError("message_text_invalid")
        parts.append(text)
    return parts


def _tool_output_texts(item: dict[str, Any]) -> list[str]:
    """Return only display text from a completed tool output record."""
    output = item.get("output")
    if isinstance(output, str):
        return [output]
    if not isinstance(output, list):
        return []
    texts: list[str] = []
    for block in output:
        if not isinstance(block, dict) or block.get("type") not in {
            "input_text",
            "output_text",
            "text",
        }:
            continue
        text = block.get("text")
        if isinstance(text, str):
            texts.append(text)
    return texts


def _receipt_candidates(text: str) -> list[dict[str, Any]]:
    """Find Engawa's one-line receipt without interpreting generic tool output."""
    found: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("kind") == "engawa.mutation_receipt":
            found.append(value)
    return found


def _validated_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schema_version") != 1:
        raise AdapterError("bridge_receipt_schema_invalid")
    logical_id = receipt.get("logical_id")
    if not isinstance(logical_id, str) or not logical_id.startswith("engawa:"):
        raise AdapterError("bridge_receipt_logical_id_invalid")

    route = receipt.get("route")
    if not isinstance(route, dict):
        raise AdapterError("bridge_receipt_route_invalid")
    route_kind = route.get("kind")
    allowed = {
        "discord-channel": ("submitted", True),
        "discord-dm": ("submitted", True),
        "discord-reply": ({"submitted", "submitted-signature-failed"}, True),
        "discord-reaction": ("reaction-applied", True),
        "cmux": ("injected", False),
        "chair-msg": ("fallback-recorded", False),
    }
    expected = allowed.get(route_kind or "")
    if expected is None:
        raise AdapterError("bridge_receipt_status_invalid")
    allowed_status = expected[0]
    if (isinstance(allowed_status, set) and receipt.get("status") not in allowed_status) or (
        isinstance(allowed_status, str) and receipt.get("status") != allowed_status
    ):
        raise AdapterError("bridge_receipt_status_invalid")
    transport_id = receipt.get("transport_id")
    if expected[1]:
        if not isinstance(transport_id, str) or not transport_id:
            raise AdapterError("bridge_receipt_transport_id_invalid")
    elif transport_id is not None:
        raise AdapterError("bridge_receipt_transport_id_invalid")

    payload = receipt.get("payload")
    if not isinstance(payload, dict) or set(payload) != {"text", "files"}:
        raise AdapterError("bridge_receipt_payload_invalid")
    text = payload.get("text")
    files = payload.get("files")
    if not isinstance(text, str) or not isinstance(files, list):
        raise AdapterError("bridge_receipt_payload_invalid")
    canonical_files: list[dict[str, Any]] = []
    for file in files:
        if not isinstance(file, dict) or set(file) != {"name", "size", "sha256"}:
            raise AdapterError("bridge_receipt_file_invalid")
        name, size, digest = file.get("name"), file.get("size"), file.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise AdapterError("bridge_receipt_file_invalid")
        canonical_files.append({"name": name, "size": size, "sha256": digest})
    canonical_payload = {"text": text, "files": canonical_files}
    encoded = json.dumps(canonical_payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if receipt.get("payload_sha256") != hashlib.sha256(encoded).hexdigest():
        raise AdapterError("bridge_receipt_digest_invalid")
    return receipt


def _receipt_text(receipt: dict[str, Any]) -> str:
    payload = receipt["payload"]
    text = str(payload["text"]) if isinstance(payload.get("text"), str) else ""
    if text and text.strip():
        return text
    files = payload["files"]
    if files:
        rendered = ", ".join(f"{file['name']} (sha256:{file['sha256']})" for file in files)
        return f"Sent attachment: {rendered}"
    if receipt["status"] == "reaction-applied":
        raise ExpectedNoCapture()
    raise AdapterError("bridge_receipt_content_missing")


# Bounded reassembly of records split by literal newlines is provided by the
# separate ``musubi_transcript_jsonl`` helper. The adapter can ship before the
# helper is installed; in that case we fall back to skip-only parsing, which
# preserves the documented corruption-tolerance behaviour.
try:
    import musubi_transcript_jsonl as _mtj  # type: ignore[import-not-found]

    _Corrupt = _mtj.Corrupt
    _iter_records = _mtj.iter_records
except ModuleNotFoundError as _exc:  # pragma: no cover - exercised by tests
    if _exc.name != "musubi_transcript_jsonl":
        raise
    _Corrupt = None
    _iter_records = None


class _SkipMarker:
    """Fallback sentinel mirroring the helper's Corrupt contract."""

    __slots__ = ("lineno", "reason")

    def __init__(self, lineno: int, reason: str) -> None:
        self.lineno = lineno
        self.reason = reason


def _records(lines: list[str]) -> Any:
    """Reassembling reader when available; otherwise today's skip semantics."""
    if _iter_records is not None:
        return _iter_records(lines)
    return _skip_only(lines)


def _skip_only(lines: list[str]) -> Any:
    for idx, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            yield _SkipMarker(idx, "unparseable")
            continue
        # Shape guard. A line can be valid JSON and NOT a record -- a bare int,
        # a string, an array. Without this the caller does record.get(...) on an
        # int and raises AttributeError, which main() does not catch, so the hook
        # dies outright.
        if isinstance(record, dict) and "type" in record:
            yield record
        else:
            yield _SkipMarker(idx, "not_a_record")


def _parse_turn(
    transcript_path: Path, turn_id: str
) -> tuple[str, str, list[dict[str, Any]] | None]:
    """Extract only user input and final assistant output for the named turn."""
    current_turn: str | None = None
    malformed_in_target = False
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    bridge_receipts: list[dict[str, Any]] = []
    try:
        lines = transcript_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AdapterError("transcript_unreadable") from exc
    for record in _records(lines):
        if not isinstance(record, dict):
            # Corrupt marker from either reader (unparseable, non-record JSON, or
            # an exhausted reassembly bound). Skipping is not enough: if the bad
            # span WAS the turn_context, keeping the old binding would attach
            # later output to a stale turn -- false memory, strictly worse than
            # the crash this replaces. Invalidate; a later valid turn_context
            # re-arms.
            if current_turn == turn_id:
                malformed_in_target = True
            current_turn = None
            continue
        if record.get("type") == "turn_context":
            payload = record.get("payload")
            current_turn = payload.get("turn_id") if isinstance(payload, dict) else None
            continue
        if current_turn != turn_id or record.get("type") != "response_item":
            continue
        message = record.get("payload")
        if not isinstance(message, dict):
            continue
        if message.get("type") in {"custom_tool_call_output", "function_call_output"}:
            for output in _tool_output_texts(message):
                bridge_receipts.extend(_receipt_candidates(output))
            continue
        if message.get("type") != "message":
            continue
        role = message.get("role")
        if role == "user":
            user_parts.extend(_text_parts(message, "input_text"))
        elif role == "assistant" and message.get("phase") == "final_answer":
            assistant_parts.extend(_text_parts(message, "output_text", allow_blank=True))
    if malformed_in_target:
        # FAIL CLOSED. Corruption inside the target turn means any capture is
        # partial. main() records this terminal reason once per attempt.
        raise AdapterError("transcript_target_turn_corrupt")
    if not user_parts:
        raise AdapterError("primary_user_text_missing")
    if len(assistant_parts) != 1:
        raise AdapterError("single_final_answer_not_proven")
    if assistant_parts[0].strip():
        return "\n\n".join(user_parts), assistant_parts[0], None
    if not bridge_receipts:
        raise AdapterError("bridge_receipt_missing")
    receipts = [_validated_receipt(receipt) for receipt in bridge_receipts]
    logical_ids = [receipt["logical_id"] for receipt in receipts]
    if len(logical_ids) != len(set(logical_ids)):
        raise AdapterError("bridge_receipt_logical_id_duplicate")
    receipt_texts: list[str] = []
    for receipt in receipts:
        try:
            receipt_texts.append(_receipt_text(receipt))
        except ExpectedNoCapture:
            continue
    if not receipt_texts:
        raise ExpectedNoCapture()
    return "\n\n".join(user_parts), "\n\n".join(receipt_texts), receipts


def parse_turn(transcript_path: Path, turn_id: str) -> tuple[str, str]:
    """Compatibility surface for tests and callers that only need turn text."""
    user_text, assistant_text, _receipt = _parse_turn(transcript_path, turn_id)
    return user_text, assistant_text


def build_envelope(hook: dict[str, Any]) -> dict[str, Any]:
    required = ("session_id", "turn_id")
    if any(not isinstance(hook.get(key), str) or not hook[key] for key in required):
        raise AdapterError("hook_identity_missing")
    actor, presence, zone = _identity_config()
    try:
        staged_prompt = read_prompt(hook)
    except ValueError as exc:
        raise AdapterError(str(exc)) from exc
    last_message = hook.get("last_assistant_message")
    if staged_prompt is not None and isinstance(last_message, str) and last_message.strip():
        user_text, assistant_text, receipts = staged_prompt, last_message, None
        capture_source = "native_hooks"
    else:
        transcript_path = hook.get("transcript_path")
        if not isinstance(transcript_path, str) or not transcript_path:
            raise AdapterError("transcript_unavailable")
        user_text, assistant_text, receipts = _parse_turn(Path(transcript_path), hook["turn_id"])
        capture_source = "transcript_fallback"
    metadata: dict[str, str] = {
        "session_id": hook["session_id"],
        "turn_id": hook["turn_id"],
        "capture_source": capture_source,
    }
    if isinstance(hook.get("model"), str) and hook["model"]:
        metadata["model"] = hook["model"]
    if isinstance(hook.get("cwd"), str) and hook["cwd"]:
        metadata["workspace"] = Path(hook["cwd"]).name
    if receipts is not None:
        metadata["bridge_receipt_count"] = str(len(receipts))
        metadata["bridge_logical_ids"] = json.dumps(
            [receipt["logical_id"] for receipt in receipts], separators=(",", ":")
        )
        metadata["bridge_transport_ids"] = json.dumps(
            [receipt["transport_id"] for receipt in receipts], separators=(",", ":")
        )
        receipt = receipts[-1]
        metadata.update(
            {
                "bridge_logical_id": receipt["logical_id"],
                "bridge_transport_id": receipt["transport_id"] or "",
                "bridge_route": receipt["route"]["kind"],
                "bridge_status": receipt["status"],
                "bridge_payload_sha256": receipt["payload_sha256"],
            }
        )
    return {
        "event_id": f"codex:{hook['session_id']}:{hook['turn_id']}",
        "actor": actor,
        "presence": presence,
        "plane": "episodic",
        "context": "primary",
        "source": "codex",
        "zone": zone,
        "user_text": user_text,
        "assistant_text": assistant_text,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Hook wiring
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    return data_root()


def _plugin_config() -> dict[str, str]:
    from musubi_harness import RuntimeConfigError

    try:
        return plugin_config()
    except RuntimeConfigError as exc:
        raise AdapterError(str(exc)) from exc


def _identity_config() -> tuple[str, str, str]:
    from musubi_harness import RuntimeConfigError

    try:
        configured = runtime_config()
    except RuntimeConfigError as exc:
        raise AdapterError(str(exc)) from exc
    return configured.actor, configured.presence, configured.zone


def _harness() -> str:
    from musubi_harness import RuntimeConfigError

    try:
        return harness_bin()
    except RuntimeConfigError as exc:
        raise AdapterError(str(exc)) from exc


def delivery_commands(
    envelope: dict[str, Any], configured: RuntimeConfig
) -> list[tuple[list[str], int]]:
    if configured.delivery_mode == "shadow":
        return []
    db = _data_root() / envelope["actor"] / envelope["zone"] / "shadow.db"
    harness = harness_bin(configured)
    memory_data = memory_data_bin(configured)
    owner = f"{envelope['actor']}-{envelope['zone']}-codex-stop"
    return [
        ([harness, "--db", str(db), "stage", "--event-id", envelope["event_id"]], 5),
        (
            [
                harness,
                "--db",
                str(db),
                "drain",
                "--once",
                "--owner",
                owner,
                "--memory-data-bin",
                memory_data,
                "--timeout",
                "5",
            ],
            15,
        ),
    ]


def _record_degraded(reason: str) -> None:
    root = _data_root()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (root / "degraded.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "reason": reason,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    except OSError:
        pass


def main() -> int:
    hook: dict[str, Any] | None = None
    retain_staged_prompt = False
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise AdapterError("hook_payload_invalid")
        hook = payload
        envelope = build_envelope(hook)
        configured = runtime_config()
        db = _data_root() / envelope["actor"] / envelope["zone"] / "shadow.db"
        retain_staged_prompt = True
        result = subprocess.run(
            [_harness(), "--db", str(db), "enqueue"],
            input=json.dumps(envelope),
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            raise AdapterError("shadow_enqueue_failed")
        retain_staged_prompt = False
        clear_prompt(hook)
        for command, timeout in delivery_commands(envelope, configured):
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=tool_environment(configured),
            )
            if result.returncode != 0:
                raise AdapterError("verified_delivery_failed")
    except ExpectedNoCapture:
        if hook is not None:
            with suppress(OSError, ValueError):
                clear_prompt(hook)
    except (
        AdapterError,
        RuntimeConfigError,
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        if hook is not None and not retain_staged_prompt:
            with suppress(OSError, ValueError):
                clear_prompt(hook)
        if isinstance(exc, (RuntimeConfigError, AdapterError)):
            reason = str(exc)
        else:
            reason = "adapter_runtime_failed"
        _record_degraded(reason)
    print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AdapterError",
    "ExpectedNoCapture",
    "build_envelope",
    "delivery_commands",
    "main",
    "parse_turn",
]
