"""Codex protocol binding for the shared Musubi MCP facade."""

from __future__ import annotations

from musubi_harness.plugin_mcp import (
    SERVER_INSTRUCTIONS,
    PluginMcpFacade,
    tool_definitions,
)

from .runtime import runtime

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
