"""Codex deployment binding for the shared Musubi plugin runtime.

The harness is the installable ``musubi-harness`` package; this module only
names the Codex state root and exposes the runtime singleton.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import time
from contextlib import suppress
from pathlib import Path

from musubi_harness.plugin_runtime import (
    PluginRuntime,
    RuntimeConfig,
    RuntimeConfigError,
)

from . import CODEX_DATA_NAME, STATE_NAME
from .data_locator import DataLocatorError, publish_data_root, resolve_data_root

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
        if self._seat_environment_mode() and not os.environ.get("MUSUBI_HARNESS_BIN"):
            return self._bundled_bin("musubi-harness")
        return super().harness_bin(config)

    def memory_data_bin(self, config: RuntimeConfig | None = None) -> str:
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
                return self._mcp_bound_root
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
            return root
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
        return root

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
        if self._seat_environment_mode():
            self._require_seat_identity(config)
            connection = self._environment_connection()
            if connection is not None:
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
