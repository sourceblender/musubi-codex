"""Codex deployment binding for the shared Musubi plugin runtime.

The harness is the installable ``musubi-harness`` package; this module only
names the Codex state root and exposes the runtime singleton.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from musubi_harness.plugin_runtime import (
    PluginRuntime,
    RuntimeConfig,
    RuntimeConfigError,
)

from . import CODEX_DATA_NAME, STATE_NAME


def _bootstrap_data_root() -> Path:
    raw = os.environ.get("PLUGIN_DATA")
    if raw:
        return Path(raw).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "plugins" / "data" / CODEX_DATA_NAME


class CodexRuntime(PluginRuntime):
    """Supply the bundled HTTP client with this plugin's private connection."""

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
        if Path(self.memory_data_bin(config)).name != "musubi-memory-data":
            return PluginRuntime.tool_environment(config)
        connection = self._connection()
        if connection is not None:
            env["MUSUBI_API_URL"], env["MUSUBI_TOKEN"] = connection
        return env

    @staticmethod
    def local_tool_environment(config: RuntimeConfig) -> dict[str, str]:
        """Run local-only harness commands without HTTP credentials."""
        env = PluginRuntime.tool_environment(config)
        env.pop("MUSUBI_API_URL", None)
        env.pop("MUSUBI_TOKEN", None)
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
]
