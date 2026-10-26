"""Immutable, non-secret project binding accepted at UserPromptSubmit."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from musubi_harness.plugin_runtime import RuntimeConfigError

from .pending import candidate_key
from .project import ProjectSettings, _read, from_snapshot


def _path(root: Path, hook: dict[str, object], *, create: bool = False) -> Path | None:
    session, turn = hook.get("session_id"), hook.get("turn_id")
    if not isinstance(session, str) or not session or not isinstance(turn, str) or not turn:
        return None
    folder = root / "turn-bindings"
    if create:
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return folder / f"{candidate_key(session, turn)}.json"
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeConfigError("project_binding_permissions_invalid")
    return folder / f"{candidate_key(session, turn)}.json"


def read_binding(root: Path, hook: dict[str, object]) -> ProjectSettings | None:
    path = _path(root, hook)
    if path is None or not (path.exists() or path.is_symlink()):
        return None
    try:
        value = json.loads(_read(path, private=True))
        if (
            not isinstance(value, dict)
            or set(value) != {"session_id", "turn_id", "path", "settings"}
            or value["session_id"] != hook.get("session_id")
            or value["turn_id"] != hook.get("turn_id")
            or not isinstance(value["path"], str)
            or not isinstance(value["settings"], dict)
        ):
            raise ValueError
        return from_snapshot(Path(value["path"]), value["settings"])
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeConfigError("project_binding_invalid") from exc


def write_binding(root: Path, hook: dict[str, object], selected: ProjectSettings) -> None:
    path = _path(root, hook, create=True)
    if path is None:
        raise RuntimeConfigError("project_turn_identity_missing")
    payload = {
        "session_id": hook["session_id"],
        "turn_id": hook["turn_id"],
        "path": str(selected.path),
        "settings": selected.snapshot(),
    }
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except FileExistsError:
            previous = read_binding(root, hook)
            if previous != selected:
                raise RuntimeConfigError("project_turn_rebind_refused") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
