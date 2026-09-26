"""First-class Codex adapter for durable Musubi capture and recall."""

from __future__ import annotations

__version__ = "0.2.0"

__all__ = [
    "CODEX_DATA_NAME",
    "STATE_NAME",
    "PluginRuntime",
    "RuntimeConfig",
    "RuntimeConfigError",
    "__version__",
]


STATE_NAME = "musubi-codex"
"""State directory name used when ``PLUGIN_DATA`` is not exported."""

CODEX_DATA_NAME = "musubi-codex"
"""Default data root name under ``~/.codex/plugins/data/`` when no env override is set."""


def __getattr__(name: str) -> object:  # pragma: no cover - thin re-export
    if name in {"PluginRuntime", "RuntimeConfig", "RuntimeConfigError"}:
        from musubi_harness.plugin_runtime import (
            PluginRuntime,
            RuntimeConfig,
            RuntimeConfigError,
        )

        return {
            "PluginRuntime": PluginRuntime,
            "RuntimeConfig": RuntimeConfig,
            "RuntimeConfigError": RuntimeConfigError,
        }[name]
    raise AttributeError(name)
