"""Codex protocol binding for the shared Musubi MCP facade."""

from __future__ import annotations

import sys

from musubi_harness.plugin_mcp import (
    SERVER_INSTRUCTIONS,
    PluginMcpFacade,
    tool_definitions,
)

from .data_locator import DataLocatorError
from .runtime import bind_installed_mcp_data_root, runtime

_facade = PluginMcpFacade(
    runtime,
    source="codex",
    event_prefix="codex",
    owner_label="codex-mcp",
    server_name="musubi-codex",
)

command_for = _facade.command_for
remember_command = _facade.remember_command
call_tool = _facade.call_tool
response_for = _facade.response_for


def main() -> int:
    try:
        bind_installed_mcp_data_root()
    except DataLocatorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _facade.serve()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SERVER_INSTRUCTIONS",
    "call_tool",
    "command_for",
    "main",
    "remember_command",
    "response_for",
    "tool_definitions",
]
