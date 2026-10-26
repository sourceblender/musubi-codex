"""Codex deployment binding for the shared Musubi plugin runtime.

The harness is the installable ``musubi-harness`` package. This adapter binds
native project/session context and deployment settings to its runtime contract.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from musubi_harness.plugin_runtime import (
    PluginRuntime,
    RuntimeConfig,
    RuntimeConfigError,
)
from musubi_harness.tokens import token_presence_problems

from . import CODEX_DATA_NAME, STATE_NAME
from .data_locator import DataLocatorError, publish_data_root, resolve_data_root
from .project import ProjectSettings, discover, load

MCP_BIND_WAIT_SECONDS = 3.0


def _bootstrap_data_root() -> Path:
    raw = os.environ.get("PLUGIN_DATA")
    if raw:
        return Path(raw).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "plugins" / "data" / CODEX_DATA_NAME


class CodexRuntime(PluginRuntime):
    """Supply the bundled HTTP client with this plugin's private connection."""

    _mcp_locator_mode = False
    _mcp_bound_root: Path | None = None
    _mcp_bound_identity: tuple[int, int] | None = None
    _project: ProjectSettings | None = None
    _bound_context: tuple[str, str | None] | None = None
    _context_error: str | None = None

    def _select_project(self, path: Path | None) -> ProjectSettings | None:
        if path is None:
            return None
        selected = load(path)
        identity = tuple(
            os.environ.get(k, "") for k in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE")
        )
        if any(identity):
            if not all(identity):
                raise RuntimeConfigError("partial_identity_config_refused")
            if identity != (selected.config.actor, selected.config.presence, selected.config.zone):
                raise RuntimeConfigError("project_launcher_identity_mismatch")
            # A complete launcher remains authoritative for connection and policy.
            return None
        return selected

    @staticmethod
    def _project_path(cwd: object) -> Path | None:
        explicit = os.environ.get("MUSUBI_CONFIG_FILE")
        if explicit:
            return Path(explicit).expanduser()
        return discover(cwd) if isinstance(cwd, str) else None

    @contextmanager
    def hook_context(self, hook: dict[str, object]) -> Iterator[None]:
        previous = self._project
        try:
            self._project = self._select_project(self._project_path(hook.get("cwd")))
            yield
        finally:
            self._project = previous

    def bind_context(self, cwd: str, session_id: str) -> dict[str, object]:
        """Idempotent connection-local binding, supplied by a native MCP tool hook."""
        try:
            if not session_id or len(session_id) > 256:
                raise RuntimeConfigError("project_session_invalid")
            explicit = os.environ.get("MUSUBI_CONFIG_FILE")
            selected = self._select_project(
                Path(explicit).expanduser() if explicit else discover(cwd)
            )
            binding = (session_id, selected.fingerprint if selected else None)
            if self._bound_context is not None and binding != self._bound_context:
                raise RuntimeConfigError("project_context_rebind_refused")
            if selected is not None:
                selected.connection()  # Validate before making this binding usable.
            self._project = selected
            self._bound_context = binding
            self._context_error = None
            return {"ok": True, "mode": "project" if selected else "legacy"}
        except RuntimeConfigError as exc:
            self._context_error = str(exc)
            raise

    def project_reference(self) -> tuple[str | None, str | None]:
        selected = self._project
        return (str(selected.path), selected.fingerprint) if selected else (None, None)

    def runtime_config(self) -> RuntimeConfig:
        if self._context_error:
            raise RuntimeConfigError(self._context_error)
        explicit = os.environ.get("MUSUBI_CONFIG_FILE")
        if self._project is None and explicit:
            self._project = self._select_project(Path(explicit).expanduser())
        if self._project is not None:
            # Detect identity/policy edits, while permitting credential rotation.
            if load(self._project.path).fingerprint != self._project.fingerprint:
                raise RuntimeConfigError("project_context_changed")
            return self._project.config
        return super().runtime_config()

    @staticmethod
    def _seat_environment_mode() -> bool:
        return bool(os.environ.get("MUSUBI_ACTOR"))

    def plugin_config(self) -> dict[str, str]:
        # Two Codex seats can share one OS user and one PLUGIN_DATA. A launcher
        # identity must not inherit another seat's file identity or policy.
        if self._seat_environment_mode():
            return {}
        return super().plugin_config()

    @staticmethod
    def _bundled_bin(name: str) -> str:
        sibling = Path(sys.executable).with_name(name)
        if sibling.is_file():
            return str(sibling)
        found = shutil.which(name)
        if found:
            return found
        raise RuntimeConfigError(f"{name.replace('-', '_')}_unavailable")

    def harness_bin(self, config: RuntimeConfig | None = None) -> str:
        if self._seat_environment_mode() or self._project is not None:
            return self._bundled_bin("musubi-harness")
        return super().harness_bin(config)

    def memory_data_bin(self, config: RuntimeConfig | None = None) -> str:
        if self._project is not None:
            self._project.connection()
            return self._bundled_bin("musubi-memory-data")
        if self._seat_environment_mode():
            self._environment_connection()  # Reject partial transport even for local status.
            return self._bundled_bin("musubi-memory-data")
        return super().memory_data_bin(config)

    @staticmethod
    def _environment_connection() -> tuple[str, str] | None:
        url = os.environ.get("MUSUBI_API_URL", "")
        token = os.environ.get("MUSUBI_TOKEN", "")
        if bool(url) != bool(token):
            raise RuntimeConfigError("connection_config_incomplete")
        if url:
            return url, token
        return None

    @staticmethod
    def _require_seat_identity(config: RuntimeConfig) -> None:
        if (
            config.actor != os.environ.get("MUSUBI_ACTOR")
            or config.presence != os.environ.get("MUSUBI_PRESENCE")
            or config.zone != os.environ.get("MUSUBI_ZONE")
        ):
            raise RuntimeConfigError("seat_identity_mismatch")

    def data_root(self) -> Path:
        if self._mcp_locator_mode:
            if self._mcp_bound_root is not None:
                try:
                    info = self._mcp_bound_root.lstat()
                except OSError:
                    raise DataLocatorError("plugin_data_locator_invalid") from None
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or (info.st_dev, info.st_ino) != self._mcp_bound_identity
                ):
                    raise DataLocatorError("plugin_data_locator_invalid")
                return self._storage_root(self._mcp_bound_root)
            codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
            deadline = time.monotonic() + MCP_BIND_WAIT_SECONDS
            while True:
                try:
                    root = resolve_data_root(codex_home, os.getcwd())
                    break
                except DataLocatorError as exc:
                    # Codex may start MCP and issue a first call while its
                    # SessionStart hook is still publishing the locator.
                    # Wait only for absence; invalid records fail immediately.
                    if (
                        str(exc) != "plugin_data_locator_unavailable"
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(0.05)
            try:
                info = root.lstat()
            except OSError:
                raise DataLocatorError("plugin_data_locator_invalid") from None
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise DataLocatorError("plugin_data_locator_invalid")
            self._mcp_bound_root = root
            self._mcp_bound_identity = (info.st_dev, info.st_ino)
            return self._storage_root(root)
        root = super().data_root()
        # Hooks receive all three values from Codex. The MCP child does not
        # receive PLUGIN_DATA, so publish the observed path under CODEX_HOME.
        plugin_data = os.environ.get("PLUGIN_DATA")
        plugin_root = os.environ.get("PLUGIN_ROOT")
        codex_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        if plugin_data and plugin_root:
            # An advisory hook must not prevent the user's session from
            # continuing. The MCP process will refuse an absent locator.
            with suppress(DataLocatorError):
                publish_data_root(codex_home, plugin_root, plugin_data)
        return self._storage_root(root)

    def _storage_root(self, root: Path) -> Path:
        if self._project is None:
            return root
        # A delivery batch must never consume a different project's outbox, even
        # when projects share actor/zone but use different presence or policy.
        root.mkdir(parents=True, exist_ok=True)
        for folder in (root / "projects", root / "projects" / self._project.fingerprint):
            folder.mkdir(mode=0o700, exist_ok=True)
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeConfigError("project_data_permissions_invalid")
        return folder

    def _connection(self) -> tuple[str, str] | None:
        path = self.data_root() / "connection.json"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return None
        except OSError:
            raise RuntimeConfigError("connection_config_invalid") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise RuntimeConfigError("connection_config_permissions_invalid")
            raw = os.read(fd, 16_385)
        except OSError:
            raise RuntimeConfigError("connection_config_invalid") from None
        finally:
            os.close(fd)
        if len(raw) > 16_384:
            raise RuntimeConfigError("connection_config_invalid")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeConfigError("connection_config_invalid") from None
        if (
            not isinstance(value, dict)
            or set(value) != {"api_url", "token"}
            or not all(isinstance(item, str) and item for item in value.values())
        ):
            raise RuntimeConfigError("connection_config_invalid")
        return value["api_url"], value["token"]

    def _codex_tool_environment(self, config: RuntimeConfig) -> dict[str, str]:
        env = self.local_tool_environment(config)
        if self._project is not None:
            if config != self.runtime_config():
                raise RuntimeConfigError("project_context_mismatch")
            project_connection = self._project.connection()
            if token_presence_problems(project_connection[1], config.presence):
                raise RuntimeConfigError("token_presence_mismatch")
            env["MUSUBI_API_URL"], env["MUSUBI_TOKEN"] = project_connection
            return env
        if self._seat_environment_mode():
            self._require_seat_identity(config)
            connection = self._environment_connection()
            if connection is not None:
                if token_presence_problems(connection[1], config.presence):
                    raise RuntimeConfigError("token_presence_mismatch")
                env["MUSUBI_API_URL"], env["MUSUBI_TOKEN"] = connection
            return env
        if Path(self.memory_data_bin(config)).name != "musubi-memory-data":
            return PluginRuntime.tool_environment(config)
        connection = self._connection()
        if connection is not None:
            env["MUSUBI_API_URL"], env["MUSUBI_TOKEN"] = connection
        return env

    def local_tool_environment(self, config: RuntimeConfig) -> dict[str, str]:
        """Run local-only commands without credentials or remote config reads."""
        if self._seat_environment_mode():
            self._require_seat_identity(config)
        env = PluginRuntime.tool_environment(config)
        env.pop("MUSUBI_API_URL", None)
        env.pop("MUSUBI_TOKEN", None)
        # Claude Code exposes option values under this compatibility name.
        # Codex does not configure it, but a shared shell may still carry it.
        env.pop("CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN", None)
        if self._seat_environment_mode() or self._project is not None:
            # cc-start historically pins the private fleet-tools binaries.
            # A standalone seat must not pass those pins to its children.
            env.pop("MUSUBI_HARNESS_BIN", None)
            env.pop("MUSUBI_MEMORY_DATA_BIN", None)
        return env

    @staticmethod
    def tool_environment(config: RuntimeConfig) -> dict[str, str]:
        # The harness defines this as a static method. Its one Codex runtime
        # singleton supplies the private data root without changing that API.
        return _runtime._codex_tool_environment(config)


_runtime = CodexRuntime(
    STATE_NAME,
    default_data_root=_bootstrap_data_root(),
)


def use_installed_mcp_locator() -> None:
    """Resolve installed MCP state lazily, after Codex lifecycle hooks run."""
    _runtime._mcp_locator_mode = True
    _runtime._mcp_bound_root = None
    _runtime._mcp_bound_identity = None
    _runtime._project = None
    _runtime._bound_context = None
    _runtime._context_error = None


# Module-level handles imported by the entry points. Re-exported here so
# downstream tests can monkey-patch them without touching sys.modules.
runtime = _runtime
data_root = _runtime.data_root
plugin_config = _runtime.plugin_config
runtime_config = _runtime.runtime_config
harness_bin = _runtime.harness_bin
memory_data_bin = _runtime.memory_data_bin
tool_environment = _runtime.tool_environment
local_tool_environment = _runtime.local_tool_environment
require_owned_namespace = _runtime.require_owned_namespace


__all__ = [
    "RuntimeConfig",
    "RuntimeConfigError",
    "data_root",
    "harness_bin",
    "local_tool_environment",
    "memory_data_bin",
    "plugin_config",
    "require_owned_namespace",
    "runtime",
    "runtime_config",
    "tool_environment",
    "use_installed_mcp_locator",
]
