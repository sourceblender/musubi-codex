"""Durable, owner-only candidates for Codex answers not yet on disk at Stop."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_ATTEMPTS = 20
MAX_AGE_SECONDS = 24 * 3600


@dataclass
class Candidate:
    session_id: str
    turn_id: str
    transcript_path: str
    created_at: float
    attempts: int = 0

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.session_id, self.turn_id, self.transcript_path)
        ):
            raise ValueError("pending_identity_invalid")
        if not Path(self.transcript_path).is_absolute():
            raise ValueError("pending_transcript_path_invalid")
        if isinstance(self.created_at, bool) or not isinstance(self.created_at, (int, float)):
            raise ValueError("pending_created_at_invalid")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or self.attempts < 0:
            raise ValueError("pending_attempts_invalid")

    @property
    def key(self) -> str:
        return candidate_key(self.session_id, self.turn_id)

    def hook(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "transcript_path": self.transcript_path,
        }


def directory(root: Path) -> Path:
    return root / "pending-exchange"


def candidate_key(session_id: str, turn_id: str) -> str:
    encoded = json.dumps([session_id, turn_id], separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def save(root: Path, candidate: Candidate) -> Path:
    folder = directory(root)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    target = folder / f"{candidate.key}.json"
    temporary = folder / f".{candidate.key}.{os.getpid()}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(candidate), handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_all(root: Path) -> list[tuple[Path, Candidate | None]]:
    folder = directory(root)
    if not folder.is_dir():
        return []
    result: list[tuple[Path, Candidate | None]] = []
    for path in folder.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            candidate = Candidate(**raw)
            if candidate.key != path.stem:
                raise ValueError("pending_key_mismatch")
        except (OSError, TypeError, ValueError):
            candidate = None
        result.append((path, candidate))
    result.sort(key=lambda pair: pair[1].created_at if pair[1] is not None else 0)
    return result


def expired(candidate: Candidate, *, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return candidate.attempts >= MAX_ATTEMPTS or now - candidate.created_at > MAX_AGE_SECONDS
