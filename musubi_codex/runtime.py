"""Codex deployment binding for the shared Musubi plugin runtime.

The harness is the installable ``musubi-harness`` package; this module only
names the Codex state root and exposes the runtime singleton.
"""

from __future__ import annotations

import os
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


_runtime = PluginRuntime(
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
require_owned_namespace = _runtime.require_owned_namespace


__all__ = [
    "RuntimeConfig",
    "RuntimeConfigError",
    "data_root",
    "harness_bin",
    "memory_data_bin",
    "plugin_config",
    "require_owned_namespace",
    "runtime",
    "runtime_config",
    "tool_environment",
]
