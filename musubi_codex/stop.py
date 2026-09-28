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
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from musubi_harness import RuntimeConfig, RuntimeConfigError

from .pending import Candidate, candidate_key, expired, load_all, save
from .prompt_stage import clear_prompt
from .runtime import (
    data_root,
    harness_bin,
    local_tool_environment,
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


def _exchange_records(transcript_path: Path, turn_id: str) -> list[dict[str, Any]]:
    """Project transcript finals onto ordered tty input spans, across turns."""
    try:
        lines = transcript_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AdapterError("transcript_unreadable") from exc
    current_turn: str | None = None
    pending: list[tuple[str, str]] = []
    seen_messages: dict[str, tuple[str, str]] = {}
    exchanges: list[dict[str, Any]] = []
    span_problem: str | None = None
    target_finals = 0
    for record in _records(lines):
        if not isinstance(record, dict):
            # A bad record can hide an input. A later final closes that
            # uncertain span; it does not poison every subsequent exchange.
            span_problem = "transcript_identity_span_corrupt"
            continue
        if record.get("type") == "turn_context":
            payload = record.get("payload")
            current_turn = payload.get("turn_id") if isinstance(payload, dict) else None
            continue
        if record.get("type") != "response_item":
            continue
        message = record.get("payload")
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        role = message.get("role")
        if role not in {"user", "assistant"} or (
            role == "assistant" and message.get("phase") != "final_answer"
        ):
            continue
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            span_problem = "host_message_id_missing"
            if role == "assistant":
                if current_turn == turn_id:
                    target_finals += 1
                    exchanges.append({"decline_reason": span_problem})
                pending.clear()
                span_problem = None
            continue
        if role == "user":
            metadata = message.get("internal_chat_message_metadata_passthrough")
            kinds = metadata.get("content_item_kinds") if isinstance(metadata, dict) else None
            if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
                span_problem = "input_provenance_missing"
                continue
            known = {"user.text", "user.image", "agents_md.instructions", "environments.environment_context", "plugins.recommendations"}
            if any(k not in known for k in kinds):
                span_problem = "input_provenance_unknown"
                continue
            try:
                parts = _text_parts(message, "input_text")
            except AdapterError as exc:
                span_problem = str(exc)
                continue
            text_value = "\n\n".join(parts)
            signature = (role, text_value)
            if message_id in seen_messages:
                if seen_messages[message_id] != signature:
                    span_problem = "host_message_id_collision"
                continue
            seen_messages[message_id] = signature
            if any(k.startswith("user.") for k in kinds):
                if not parts:
                    span_problem = "primary_user_text_missing"
                    continue
                pending.append((message_id, text_value))
            continue
        try:
            parts = _text_parts(message, "output_text", allow_blank=True)
        except AdapterError as exc:
            span_problem = str(exc)
            parts = []
        if len(parts) != 1:
            span_problem = "final_answer_text_invalid"
            if current_turn == turn_id:
                target_finals += 1
                exchanges.append({"decline_reason": span_problem})
            pending.clear()
            span_problem = None
            continue
        signature = (role, parts[0])
        if message_id in seen_messages:
            if seen_messages[message_id] != signature:
                if current_turn == turn_id:
                    target_finals += 1
                    exchanges.append({"decline_reason": "host_message_id_collision"})
                pending.clear()
                span_problem = None
            continue
        seen_messages[message_id] = signature
        if current_turn == turn_id:
            target_finals += 1
            if span_problem:
                exchanges.append({"decline_reason": span_problem})
            else:
                exchanges.append(
                    {"answer_id": message_id, "assistant_text": parts[0],
                     "inputs": list(pending), "turn_id": turn_id}
                )
        pending.clear()
        span_problem = None
    if not target_finals:
        raise AdapterError("single_final_answer_not_proven")
    return exchanges


def build_envelopes(
    hook: dict[str, Any], *, declines: list[str] | None = None
) -> list[dict[str, Any]]:
    required = ("session_id", "turn_id")
    if any(not isinstance(hook.get(key), str) or not hook[key] for key in required):
        raise AdapterError("hook_identity_missing")
    actor, presence, zone = _identity_config()
    transcript_path = hook.get("transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        raise AdapterError("transcript_unavailable")
    exchanges = _exchange_records(Path(transcript_path), hook["turn_id"])
    envelopes: list[dict[str, Any]] = []
    for exchange in exchanges:
        if "decline_reason" in exchange:
            if declines is not None:
                declines.append(exchange["decline_reason"])
            continue
        inputs = exchange["inputs"]
        if not inputs:
            if declines is not None:
                declines.append("no_eligible_input")
            continue
        assistant_text = exchange["assistant_text"]
        receipts: list[dict[str, Any]] | None = None
        if not assistant_text.strip():
            # A blank final can represent an Engawa action. Preserve the
            # receipt-backed recovery path only when the old turn parser can
            # prove one final; multiple blank finals need finer receipt
            # association and must fail closed.
            try:
                _user, assistant_text, receipts = _parse_turn(
                    Path(transcript_path), hook["turn_id"]
                )
            except ExpectedNoCapture:
                continue
            except AdapterError as exc:
                if declines is not None:
                    declines.append(str(exc))
                continue
        input_ids = [item[0] for item in inputs]
        encoded_ids = json.dumps(input_ids, ensure_ascii=False, separators=(",", ":"))
        encoded_texts = json.dumps(
            [item[1] for item in inputs], ensure_ascii=False, separators=(",", ":")
        )
        metadata: dict[str, str] = {
            "session_id": hook["session_id"],
            "turn_id": hook["turn_id"],
            "capture_source": "transcript_exchange",
            "answer_id": exchange["answer_id"],
            "input_record_texts_sha256": hashlib.sha256(encoded_texts.encode("utf-8")).hexdigest(),
        }
        if len(encoded_ids.encode("utf-8")) <= 1024:
            metadata["input_record_ids"] = encoded_ids
        else:
            metadata["input_record_ids_sha256"] = hashlib.sha256(encoded_ids.encode("utf-8")).hexdigest()
            metadata["input_record_count"] = str(len(input_ids))
        if receipts is not None:
            metadata["bridge_receipt_count"] = str(len(receipts))
            metadata["bridge_logical_ids"] = json.dumps(
                [receipt["logical_id"] for receipt in receipts], separators=(",", ":")
            )
            metadata["bridge_transport_ids"] = json.dumps(
                [receipt["transport_id"] for receipt in receipts], separators=(",", ":")
            )
            receipt = receipts[-1]
            metadata.update({
                "bridge_logical_id": receipt["logical_id"],
                "bridge_transport_id": receipt["transport_id"] or "",
                "bridge_route": receipt["route"]["kind"],
                "bridge_status": receipt["status"],
                "bridge_payload_sha256": receipt["payload_sha256"],
            })
        envelopes.append({
            "event_id": f"exchange.v1:codex:{hook['session_id']}:{exchange['answer_id']}",
            "actor": actor,
            "presence": presence,
            "plane": "episodic",
            "context": "primary",
            "source": "codex",
            "zone": zone,
            "user_text": "\n\n".join(item[1] for item in inputs),
            "assistant_text": assistant_text,
            "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "metadata": metadata,
        })
    return envelopes


def build_envelope(hook: dict[str, Any]) -> dict[str, Any]:
    """Compatibility helper for a hook resolving exactly one exchange."""
    declines: list[str] = []
    envelopes = build_envelopes(hook, declines=declines)
    if not envelopes and declines:
        raise AdapterError(declines[0])
    if len(envelopes) != 1:
        raise AdapterError("multiple_exchange_envelopes")
    return envelopes[0]


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
                "--max",
                "5",
                "--budget-seconds",
                "3",
            ],
            24,
        ),
    ]


def _legacy_drain_retry(command: list[str], result: subprocess.CompletedProcess[str]) -> bool:
    """An explicitly older harness may lack batch flags; retry its original one-row command."""
    lines = result.stderr.splitlines()
    argparse_error = "musubi-harness: error: unrecognized arguments: --max 5 --budget-seconds 3"
    return (
        "--memory-data-bin" in command
        and command[-4:] == ["--max", "5", "--budget-seconds", "3"]
        and result.returncode == 2
        and not result.stdout
        and bool(lines)
        and lines[-1] == argparse_error
        and all(line.startswith(("usage:", " ")) for line in lines[:-1])
    )


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


_NOT_YET = {"single_final_answer_not_proven", "transcript_unreadable"}
_POLL_ATTEMPTS = 5
_POLL_INTERVAL = 0.2
_PENDING_LIMIT = 10
_PENDING_BUDGET = 4.0


def _pending_root(configured: RuntimeConfig) -> Path:
    return _data_root() / configured.actor / configured.zone


def _enqueue(envelope: dict[str, Any], configured: RuntimeConfig) -> None:
    db = _pending_root(configured) / "shadow.db"
    result = subprocess.run(
        [_harness(), "--db", str(db), "enqueue"],
        input=json.dumps(envelope),
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
        env=local_tool_environment(configured),
    )
    if result.returncode != 0:
        raise AdapterError("shadow_enqueue_failed")


def _candidate_from_hook(hook: dict[str, Any]) -> Candidate:
    for field in ("session_id", "turn_id", "transcript_path"):
        if not isinstance(hook.get(field), str) or not hook[field]:
            raise AdapterError("hook_identity_missing" if field != "transcript_path" else "transcript_unavailable")
    return Candidate(
        session_id=hook["session_id"], turn_id=hook["turn_id"],
        transcript_path=hook["transcript_path"], created_at=time.time(),
    )


def _capture_current(
    hook: dict[str, Any], configured: RuntimeConfig
) -> list[dict[str, Any]]:
    candidate = _candidate_from_hook(hook)
    root = _pending_root(configured)
    # The Stop hook can be killed while polling or enqueuing. Persist first.
    save(root, candidate)
    for attempt in range(_POLL_ATTEMPTS):
        declines: list[str] = []
        try:
            envelopes = build_envelopes(candidate.hook(), declines=declines)
        except AdapterError as exc:
            if str(exc) not in _NOT_YET:
                raise
            if attempt + 1 < _POLL_ATTEMPTS:
                time.sleep(_POLL_INTERVAL)
                continue
            return []
        for reason in declines:
            _record_degraded(reason)
        for envelope in envelopes:
            try:
                _enqueue(envelope, configured)
            except (AdapterError, OSError, subprocess.SubprocessError):
                save(root, candidate)
                raise
        if configured.delivery_mode == "shadow":
            (root / "pending-exchange" / f"{candidate.key}.json").unlink(missing_ok=True)
        return envelopes
    raise AssertionError("poll loop exhausted")


def drain_pending(
    configured: RuntimeConfig, *, skip_key: str | None = None
) -> list[dict[str, Any]]:
    """Enqueue older candidates independently; one failure cannot block the rest."""
    root = _pending_root(configured)
    deadline = time.monotonic() + _PENDING_BUDGET
    captured: list[dict[str, Any]] = []
    handled = 0
    for path, candidate in load_all(root):
        if handled >= _PENDING_LIMIT or time.monotonic() >= deadline:
            break
        if candidate is not None and candidate.key == skip_key:
            continue
        handled += 1
        if candidate is None:
            _record_degraded("pending_unreadable")
            path.unlink(missing_ok=True)
            continue
        declines: list[str] = []
        try:
            envelopes = build_envelopes(candidate.hook(), declines=declines)
        except AdapterError as exc:
            if str(exc) in _NOT_YET:
                candidate.attempts += 1
                if expired(candidate):
                    _record_degraded("pending_expired")
                    path.unlink(missing_ok=True)
                else:
                    save(root, candidate)
                continue
            _record_degraded(str(exc))
            path.unlink(missing_ok=True)
            continue
        for reason in declines:
            _record_degraded(reason)
        failed = False
        candidate_captured: list[dict[str, Any]] = []
        for envelope in envelopes:
            try:
                _enqueue(envelope, configured)
            except (AdapterError, OSError, subprocess.SubprocessError):
                _record_degraded("shadow_enqueue_failed")
                failed = True
                break
            candidate_captured.append(envelope)
        if failed:
            candidate.attempts += 1
            if expired(candidate):
                _record_degraded("pending_expired")
                path.unlink(missing_ok=True)
            else:
                save(root, candidate)
            continue
        captured.extend(candidate_captured)
        if configured.delivery_mode == "shadow":
            path.unlink(missing_ok=True)
        # Leave room for the current Stop and the verified-delivery pass.
        # A later hook drains the next candidate in the backlog.
        if candidate_captured:
            break
    return captured


def _deliver(
    envelopes: list[dict[str, Any]], configured: RuntimeConfig, *, drain_only: bool = False
) -> None:
    if configured.delivery_mode == "shadow" or not envelopes:
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    for envelope in envelopes:
        metadata = envelope.get("metadata", {})
        if isinstance(metadata, dict) and isinstance(metadata.get("session_id"), str) and isinstance(metadata.get("turn_id"), str):
            key = candidate_key(metadata["session_id"], metadata["turn_id"])
        else:
            key = envelope["event_id"]  # compatibility with synthetic test envelopes
        groups.setdefault(key, []).append(envelope)
    staged = False
    for key, group in groups.items():
        all_staged = True
        for envelope in group:
            command, timeout = delivery_commands(envelope, configured)[0]
            try:
                result = subprocess.run(
                    command, text=True, capture_output=True, timeout=timeout,
                    check=False, env=local_tool_environment(configured),
                )
                if result.returncode != 0:
                    raise AdapterError("verified_delivery_failed")
                staged = True
            except (AdapterError, OSError, subprocess.SubprocessError):
                _record_degraded("verified_delivery_failed")
                all_staged = False
        if all_staged:
            (_pending_root(configured) / "pending-exchange" / f"{key}.json").unlink(missing_ok=True)
    if not staged or drain_only:
        return
    pair = delivery_commands(envelopes[-1], configured)
    if len(pair) < 2:
        return
    command, timeout = pair[1]
    try:
        environment = tool_environment(configured)
        result = subprocess.run(
            command, text=True, capture_output=True, timeout=timeout,
            check=False, env=environment,
        )
        if _legacy_drain_retry(command, result):
            result = subprocess.run(
                command[:-4], text=True, capture_output=True,
                timeout=15, check=False, env=environment,
            )
        if result.returncode != 0:
            raise AdapterError("verified_delivery_failed")
    except (AdapterError, OSError, subprocess.SubprocessError):
        _record_degraded("verified_delivery_failed")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    drain_only = "--drain-only" in args
    hook: dict[str, Any] | None = None
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise AdapterError("hook_payload_invalid")
        hook = payload
        configured = runtime_config()
        skip_key: str | None = None
        if not drain_only:
            with suppress(AdapterError):
                skip_key = _candidate_from_hook(hook).key
        captured = drain_pending(configured, skip_key=skip_key)
        if not drain_only:
            try:
                captured.extend(_capture_current(hook, configured))
            except (AdapterError, OSError, subprocess.SubprocessError) as exc:
                _record_degraded(str(exc) if isinstance(exc, AdapterError) else "adapter_runtime_failed")
            finally:
                with suppress(OSError, ValueError):
                    clear_prompt(hook)
        _deliver(captured, configured, drain_only=drain_only)
    except (
        AdapterError,
        RuntimeConfigError,
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
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
    "build_envelopes",
    "delivery_commands",
    "main",
    "parse_turn",
]
