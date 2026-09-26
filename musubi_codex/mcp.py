"""Codex protocol binding for the shared Musubi MCP facade."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from typing import Any, TextIO

from musubi_harness.plugin_mcp import (
    SERVER_INSTRUCTIONS,
    PluginMcpFacade,
    tool_definitions,
)
from musubi_harness.plugin_runtime import RuntimeConfig, RuntimeConfigError

from .data_locator import DataLocatorError
from .runtime import runtime, use_installed_mcp_locator

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


def _unavailable(request_id: object, detail: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"ok": False, "status": "unavailable", "detail": detail}),
                }
            ],
            "isError": True,
        },
    }


def serve(*, stdin: Iterable[str], stdout: TextIO) -> int:
    """Advertise tools before hooks run; resolve host data for each call."""
    use_installed_mcp_locator()
    # initialize, ping and tools/list do not use the runtime config. Give the
    # shared protocol handler a valid placeholder for those methods only.
    placeholder = RuntimeConfig(actor="unavailable", presence="unavailable/mcp", zone="home")
    for line in stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError
            method = request.get("method")
            params = request.get("params")
            if (
                method == "tools/call"
                and request.get("id") is not None
                and isinstance(params, dict)
                and isinstance(params.get("name"), str)
            ):
                try:
                    configured = runtime.runtime_config()
                except (DataLocatorError, RuntimeConfigError) as exc:
                    response: dict[str, Any] | None = _unavailable(request.get("id"), str(exc))
                else:
                    response = _facade.response_for(request, configured)
            else:
                response = _facade.response_for(request, placeholder)
        except DataLocatorError as exc:
            response = _unavailable(request.get("id"), str(exc))
        except (json.JSONDecodeError, ValueError):
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if response is not None:
            print(json.dumps(response, separators=(",", ":")), file=stdout, flush=True)
    return 0


def main() -> int:
    return serve(stdin=sys.stdin, stdout=sys.stdout)


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
