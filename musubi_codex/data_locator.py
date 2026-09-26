"""Join an installed Codex hook and MCP server to one host-owned data root.

Codex CLI 0.157.1 gives legacy hooks ``PLUGIN_DATA`` and ``PLUGIN_ROOT``, but
does not forward either to the legacy MCP child. The MCP manifest runs with
``cwd: '.'`` at that same installed plugin root and forwards ``CODEX_HOME``.
This short-lived, per-install locator bridges only those measured surfaces.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from contextlib import suppress
from pathlib import Path

MAX_AGE_SECONDS = 600
MAX_FILE_BYTES = 4096


class DataLocatorError(ValueError):
    """The installed plugin's data root cannot be established safely."""


def _context(codex_home: str, plugin_root: str) -> tuple[Path, Path, Path]:
    try:
        home = Path(codex_home).expanduser().resolve(strict=True)
        root = Path(plugin_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise DataLocatorError("plugin_data_locator_invalid") from None
    if not home.is_dir() or not root.is_dir():
        raise DataLocatorError("plugin_data_locator_invalid")
    data_parent = home / "plugins" / "data"
    if not data_parent.is_dir():
        raise DataLocatorError("plugin_data_locator_unavailable")
    digest = hashlib.sha256(os.fsencode(root)).hexdigest()
    return home, root, data_parent / ".musubi-codex-locators" / f"{digest}.json"


def _owned_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def _owned_data_directory(path: Path) -> bool:
    """Accept Codex-created data dirs, which may be readable but not writable by peers."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022


def publish_data_root(codex_home: str, plugin_root: str, plugin_data: str) -> None:
    """Atomically publish the hook's actual data root for this installation."""
    # The first SessionStart can run before Codex creates PLUGIN_DATA. Make
    # only the host-assigned direct child, never a guessed fallback or an
    # arbitrary path supplied through the environment.
    try:
        home_path = Path(codex_home).expanduser().resolve(strict=True)
        data_path = Path(plugin_data).expanduser()
        parent = home_path / "plugins" / "data"
        if not data_path.is_absolute() or data_path.parent != parent:
            raise DataLocatorError("plugin_data_locator_invalid")
        plugins = parent.parent
        plugins_info = plugins.lstat()
        if not stat.S_ISDIR(plugins_info.st_mode) or plugins_info.st_uid != os.getuid():
            raise DataLocatorError("plugin_data_locator_invalid")
        parent.mkdir(mode=0o700, exist_ok=True)
        parent_info = parent.lstat()
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid():
            raise DataLocatorError("plugin_data_locator_invalid")
        data_path.mkdir(mode=0o700, exist_ok=True)
        if not _owned_data_directory(data_path):
            raise DataLocatorError("plugin_data_locator_invalid")
    except (OSError, RuntimeError):
        raise DataLocatorError("plugin_data_locator_invalid") from None
    home, root, locator = _context(codex_home, plugin_root)
    try:
        data = Path(plugin_data).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise DataLocatorError("plugin_data_locator_invalid") from None
    if not data.is_relative_to(home / "plugins" / "data") or not data.is_dir():
        raise DataLocatorError("plugin_data_locator_invalid")
    try:
        if data.stat().st_uid != os.getuid():
            raise DataLocatorError("plugin_data_locator_invalid")
    except OSError:
        raise DataLocatorError("plugin_data_locator_invalid") from None
    try:
        locator.parent.mkdir(mode=0o700, exist_ok=True)
    except OSError:
        raise DataLocatorError("plugin_data_locator_invalid") from None
    if not _owned_directory(locator.parent):
        raise DataLocatorError("plugin_data_locator_invalid")

    payload = json.dumps(
        {"plugin_root": str(root), "data_root": str(data), "created": time.time()},
        separators=(",", ":"),
    ).encode()
    if len(payload) > MAX_FILE_BYTES:
        raise DataLocatorError("plugin_data_locator_invalid")
    fd = -1
    temporary = ""
    try:
        fd, temporary = tempfile.mkstemp(prefix=".new-", dir=locator.parent)
        os.fchmod(fd, 0o600)
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, locator)
    except OSError:
        raise DataLocatorError("plugin_data_locator_invalid") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary:
            with suppress(FileNotFoundError):
                os.unlink(temporary)


def resolve_data_root(codex_home: str, plugin_root: str) -> Path:
    """Load only a fresh locator for the MCP process's exact installed root."""
    home, root, locator = _context(codex_home, plugin_root)
    if not _owned_directory(locator.parent):
        raise DataLocatorError("plugin_data_locator_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(locator, flags)
    except OSError:
        raise DataLocatorError("plugin_data_locator_unavailable") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise DataLocatorError("plugin_data_locator_invalid")
        raw = os.read(fd, MAX_FILE_BYTES + 1)
    except OSError:
        raise DataLocatorError("plugin_data_locator_invalid") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_FILE_BYTES:
        raise DataLocatorError("plugin_data_locator_invalid")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"plugin_root", "data_root", "created"}:
            raise ValueError
        if value["plugin_root"] != str(root) or not isinstance(value["created"], (int, float)):
            raise ValueError
        age = time.time() - value["created"]
        if not 0 <= age <= MAX_AGE_SECONDS:
            raise ValueError
        data = Path(value["data_root"]).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError):
        raise DataLocatorError("plugin_data_locator_invalid") from None
    if not data.is_relative_to(home / "plugins" / "data") or not data.is_dir():
        raise DataLocatorError("plugin_data_locator_invalid")
    try:
        if data.stat().st_uid != os.getuid():
            raise DataLocatorError("plugin_data_locator_invalid")
    except OSError:
        raise DataLocatorError("plugin_data_locator_invalid") from None
    return data
