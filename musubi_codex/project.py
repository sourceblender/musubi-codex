"""Explicit project settings; no dotenv execution or process-wide environment mutation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from musubi_harness.plugin_runtime import SEGMENT, RuntimeConfig, RuntimeConfigError


def _read(path: Path, *, private: bool) -> str:
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            info = os.fstat(fd)
            mask = 0o077 if private else 0o022
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & mask:
                raise RuntimeConfigError("project_file_permissions_invalid")
            raw = os.read(fd, 65_537)
        finally:
            os.close(fd)
        if len(raw) > 65_536:
            raise RuntimeConfigError("project_file_too_large")
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeConfigError("project_file_unreadable") from exc


def discover(cwd: str) -> Path | None:
    """Find the nearest settings file, without crossing a Git worktree boundary."""
    directory = Path(cwd)
    if not directory.is_absolute() or not directory.is_dir():
        raise RuntimeConfigError("project_cwd_invalid")
    directory = directory.resolve()
    for parent in (directory, *directory.parents):
        path = parent / ".codex" / "musubi.toml"
        if path.exists() or path.is_symlink():
            return path
        if (parent / ".git").exists():
            break
    return None


@dataclass(frozen=True)
class ProjectSettings:
    path: Path
    config: RuntimeConfig
    env_file: Path
    prompt_recall: bool

    def snapshot(self) -> dict[str, object]:
        """Non-secret settings sufficient to identify an accepted turn's context."""
        return {
            "schema_version": 1,
            "actor": self.config.actor,
            "presence": self.config.presence,
            "zone": self.config.zone,
            "env_file": str(self.env_file),
            "delivery_mode": self.config.delivery_mode,
            "prompt_recall": self.prompt_recall,
        }

    @property
    def fingerprint(self) -> str:
        # Rotation of the secret at this location must not orphan pending captures.
        value = [
            str(self.path),
            self.config.actor,
            self.config.presence,
            self.config.zone,
            self.config.delivery_mode,
            str(self.env_file),
            self.prompt_recall,
        ]
        return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()

    def connection(self) -> tuple[str, str]:
        values: dict[str, str] = {}
        for line in _read(self.env_file, private=True).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            key, separator, raw = line.partition("=")
            key = key.strip()
            # Mixed project .env files are allowed; unrelated keys are never loaded.
            if not key.startswith("MUSUBI_"):
                continue
            if not separator or not re.fullmatch(r"MUSUBI_[A-Z_]+", key) or key in values:
                raise RuntimeConfigError("project_env_invalid")
            try:
                parts = shlex.split(raw, comments=True)
            except ValueError as exc:
                raise RuntimeConfigError("project_env_invalid") from exc
            if len(parts) != 1 or "${" in parts[0] or parts[0].startswith("op://"):
                raise RuntimeConfigError("project_env_value_unsupported")
            values[key] = parts[0]
        for key, expected in (
            ("MUSUBI_ACTOR", self.config.actor),
            ("MUSUBI_PRESENCE", self.config.presence),
            ("MUSUBI_ZONE", self.config.zone),
            ("MUSUBI_DELIVERY_MODE", self.config.delivery_mode),
            ("MUSUBI_PROMPT_RECALL", str(self.prompt_recall).lower()),
        ):
            if key in values and values[key] != expected:
                raise RuntimeConfigError("project_env_identity_or_policy_mismatch")
        url, token = values.get("MUSUBI_API_URL", ""), values.get("MUSUBI_TOKEN", "")
        if not url or not token:
            raise RuntimeConfigError("project_connection_incomplete")
        return url, token


def load(path: Path) -> ProjectSettings:
    if not path.is_absolute():
        raise RuntimeConfigError("project_config_path_invalid")
    try:
        raw = tomllib.loads(_read(path, private=False))
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeConfigError("project_config_invalid") from exc
    return from_snapshot(path, raw)


def from_snapshot(path: Path, raw: dict[str, object]) -> ProjectSettings:
    """Validate durable non-secret settings without rereading a changed config file."""
    if not path.is_absolute():
        raise RuntimeConfigError("project_config_path_invalid")
    allowed = {
        "schema_version",
        "actor",
        "presence",
        "zone",
        "env_file",
        "delivery_mode",
        "prompt_recall",
    }
    if (
        set(raw) - allowed
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 1
    ):
        raise RuntimeConfigError("project_config_invalid")
    for key in ("actor", "presence", "zone", "env_file"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise RuntimeConfigError("project_config_invalid")
    actor, presence, zone = str(raw["actor"]), str(raw["presence"]), str(raw["zone"])
    parts = presence.split("/")
    if (
        len(parts) != 2
        or parts[0] != actor
        or not all(SEGMENT.fullmatch(p) for p in [actor, *parts])
        or zone not in {"home", "work"}
    ):
        raise RuntimeConfigError("project_identity_invalid")
    delivery = raw.get("delivery_mode", "shadow")
    recall = raw.get("prompt_recall", False)
    if delivery not in ("shadow", "verified") or not isinstance(recall, bool):
        raise RuntimeConfigError("project_policy_invalid")
    env_file = Path(str(raw["env_file"])).expanduser()
    if not env_file.is_absolute():
        env_file = path.parent / env_file
    return ProjectSettings(
        path.absolute(),
        RuntimeConfig(
            actor=str(actor), presence=str(presence), zone=str(zone), delivery_mode=str(delivery)
        ),
        env_file.absolute(),
        recall,
    )
