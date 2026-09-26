"""Installed Codex hook/MCP data-root handoff and refusal boundaries."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

import musubi_codex.runtime as runtime_module
from musubi_codex import mcp
from musubi_codex.data_locator import (
    DataLocatorError,
    publish_data_root,
    resolve_data_root,
)
from musubi_codex.runtime import CodexRuntime, runtime


def _installation(tmp_path: Path, name: str = "one") -> tuple[Path, Path, Path]:
    home = tmp_path / "codex-home"
    root = home / "plugins" / "cache" / name
    data = home / "plugins" / "data" / name
    root.mkdir(parents=True)
    data.mkdir(parents=True)
    return home, root, data


def test_hook_publishes_exact_root_for_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, root, data = _installation(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("PLUGIN_ROOT", str(root))
    monkeypatch.setenv("PLUGIN_DATA", str(data))

    assert CodexRuntime("musubi-codex").data_root() == data
    monkeypatch.delenv("PLUGIN_DATA")
    assert resolve_data_root(str(home), str(root)) == data


def test_first_hook_creates_only_host_assigned_data_dir(tmp_path: Path) -> None:
    home = tmp_path / "codex-home"
    root = home / "plugins" / "cache" / "one"
    root.mkdir(parents=True)
    data = home / "plugins" / "data" / "one"
    with pytest.raises(DataLocatorError, match="unavailable"):
        resolve_data_root(str(home), str(root))
    publish_data_root(str(home), str(root), str(data))
    assert data.is_dir()
    assert data.stat().st_mode & 0o077 == 0
    assert resolve_data_root(str(home), str(root)) == data
    with pytest.raises(DataLocatorError, match="invalid"):
        publish_data_root(str(home), str(root), str(tmp_path / "outside"))


def test_locator_is_keyed_to_installed_plugin_root(tmp_path: Path) -> None:
    home, first, first_data = _installation(tmp_path)
    _home, second, second_data = _installation(tmp_path, "two")
    publish_data_root(str(home), str(first), str(first_data))
    with pytest.raises(DataLocatorError, match="unavailable"):
        resolve_data_root(str(home), str(second))
    publish_data_root(str(home), str(second), str(second_data))
    assert resolve_data_root(str(home), str(first)) == first_data
    assert resolve_data_root(str(home), str(second)) == second_data


def test_locator_refuses_stale_and_future_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setattr("musubi_codex.data_locator.time.time", lambda: 10**12)
    with pytest.raises(DataLocatorError, match="invalid"):
        resolve_data_root(str(home), str(root))
    monkeypatch.setattr("musubi_codex.data_locator.time.time", lambda: 0)
    with pytest.raises(DataLocatorError, match="invalid"):
        resolve_data_root(str(home), str(root))


def test_locator_refuses_symlink_and_world_readable_file(tmp_path: Path) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    locator = next((home / "plugins" / "data" / ".musubi-codex-locators").glob("*.json"))
    locator.chmod(0o644)
    with pytest.raises(DataLocatorError, match="invalid"):
        resolve_data_root(str(home), str(root))
    locator.chmod(0o600)
    copy = locator.with_suffix(".copy")
    copy.write_bytes(locator.read_bytes())
    locator.unlink()
    locator.symlink_to(copy)
    with pytest.raises(DataLocatorError):
        resolve_data_root(str(home), str(root))


def test_locator_refuses_outside_target_and_wrong_root(tmp_path: Path) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    locator = next((home / "plugins" / "data" / ".musubi-codex-locators").glob("*.json"))
    record = json.loads(locator.read_text())
    record["plugin_root"] = str(tmp_path / "other")
    locator.write_text(json.dumps(record))
    with pytest.raises(DataLocatorError, match="invalid"):
        resolve_data_root(str(home), str(root))
    record["plugin_root"] = str(root)
    record["data_root"] = str(tmp_path)
    locator.write_text(json.dumps(record))
    with pytest.raises(DataLocatorError, match="invalid"):
        resolve_data_root(str(home), str(root))


def test_hook_keeps_own_data_root_when_locator_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("PLUGIN_ROOT", str(root))
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    locator_dir = home / "plugins" / "data" / ".musubi-codex-locators"
    locator_dir.mkdir(mode=0o755)
    assert CodexRuntime("musubi-codex").data_root() == data
    with pytest.raises(DataLocatorError, match="unavailable"):
        resolve_data_root(str(home), str(root))


def test_mcp_manifest_forwards_home_and_uses_installed_root() -> None:
    manifest = json.loads((Path(__file__).parents[1] / ".mcp.json").read_text())
    server = manifest["mcpServers"]["musubi-codex"]
    assert server["cwd"] == "."
    assert "CODEX_HOME" in server["env_vars"]
    assert "PLUGIN_DATA" not in server["env_vars"]
    assert "PLUGIN_ROOT" not in server["env_vars"]


def test_mcp_advertises_tools_before_hook_locator_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, _data = _installation(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.setattr(runtime_module, "MCP_BIND_WAIT_SECONDS", 0)
    for name in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)
    requests = io.StringIO(
        "\n".join(
            json.dumps(value)
            for value in (
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "musubi_status", "arguments": {}},
                },
            )
        )
        + "\n"
    )
    output = io.StringIO()
    assert mcp.serve(stdin=requests, stdout=output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "musubi-codex"
    assert any(tool["name"] == "musubi_status" for tool in responses[1]["result"]["tools"])
    assert responses[2]["result"]["isError"] is True
    assert "plugin_data_locator_unavailable" in responses[2]["result"]["content"][0]["text"]


def test_mcp_uses_hook_data_root_after_locator_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    (data / "config.json").write_text(
        json.dumps({"actor": "proof", "presence": "proof/test", "zone": "home"})
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.setattr(runtime_module, "MCP_BIND_WAIT_SECONDS", 0)
    for name in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)
    monkeypatch.setattr(runtime, "default_data_root", tmp_path / "wrong-fallback")
    monkeypatch.setattr(
        mcp._facade,
        "call_tool",
        lambda config, _name, _arguments: {
            "structuredContent": {"actor": config.actor, "data_root": str(runtime.data_root())},
            "isError": False,
        },
    )

    def stream() -> Iterator[str]:
        yield json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        yield json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "musubi_status", "arguments": {}},
            }
        )
        publish_data_root(str(home), str(root), str(data))
        yield json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "musubi_status", "arguments": {}},
            }
        )

    output = io.StringIO()
    assert mcp.serve(stdin=stream(), stdout=output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[1]["result"]["isError"] is True
    assert "plugin_data_locator_unavailable" in responses[1]["result"]["content"][0]["text"]
    assert responses[2]["result"]["structuredContent"] == {"actor": "proof", "data_root": str(data)}
    assert runtime.data_root() == data


def test_default_codex_home_works_without_exported_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "user"
    home = user_home / ".codex"
    root = home / "plugins" / "cache" / "one"
    data = home / "plugins" / "data" / "one"
    root.mkdir(parents=True)
    data.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("PLUGIN_ROOT", str(root))
    monkeypatch.setenv("PLUGIN_DATA", str(data))
    assert CodexRuntime("musubi-codex").data_root() == data
    monkeypatch.delenv("PLUGIN_DATA")
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)
    monkeypatch.setattr(runtime, "default_data_root", tmp_path / "wrong-fallback")
    output = io.StringIO()
    assert (
        mcp.serve(
            stdin=io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
            ),
            stdout=output,
        )
        == 0
    )
    assert runtime.data_root() == data


def test_locator_disappearing_during_tool_call_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    (data / "config.json").write_text(
        json.dumps({"actor": "proof", "presence": "proof/test", "zone": "home"})
    )
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    for name in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)

    def lost(_request: object, _config: object) -> None:
        raise DataLocatorError("plugin_data_locator_unavailable")

    monkeypatch.setattr(mcp._facade, "response_for", lost)
    output = io.StringIO()
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "musubi_status", "arguments": {}},
    }
    assert mcp.serve(stdin=io.StringIO(json.dumps(request) + "\n"), stdout=output) == 0
    response = json.loads(output.getvalue())
    assert response["result"]["isError"] is True
    assert "plugin_data_locator_unavailable" in response["result"]["content"][0]["text"]


def test_bound_mcp_process_survives_locator_expiry_but_refuses_replaced_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.chdir(root)
    installed = CodexRuntime("musubi-codex")
    installed._mcp_locator_mode = True
    assert installed.data_root() == data
    locator = next((home / "plugins" / "data" / ".musubi-codex-locators").glob("*.json"))
    locator.unlink()
    monkeypatch.setattr("musubi_codex.data_locator.time.time", lambda: 10**12)
    assert installed.data_root() == data
    data.rename(data.with_name("old-data"))
    data.mkdir()
    with pytest.raises(DataLocatorError, match="invalid"):
        installed.data_root()


def test_first_mcp_bind_waits_for_missing_locator_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.chdir(root)
    installed = CodexRuntime("musubi-codex")
    installed._mcp_locator_mode = True
    attempts = 0
    sleeps: list[float] = []
    real_resolve = runtime_module.resolve_data_root

    def delayed(codex_home: str, plugin_root: str) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise DataLocatorError("plugin_data_locator_unavailable")
        return real_resolve(codex_home, plugin_root)

    monkeypatch.setattr(runtime_module, "resolve_data_root", delayed)
    monkeypatch.setattr(runtime_module.time, "sleep", sleeps.append)
    assert installed.data_root() == data
    assert attempts == 3
    assert sleeps == [0.05, 0.05]

    def invalid(_codex_home: str, _plugin_root: str) -> Path:
        raise DataLocatorError("plugin_data_locator_invalid")

    another = CodexRuntime("musubi-codex")
    another._mcp_locator_mode = True
    monkeypatch.setattr(runtime_module, "resolve_data_root", invalid)
    with pytest.raises(DataLocatorError, match="invalid"):
        another.data_root()
    assert sleeps == [0.05, 0.05]


def test_mcp_startup_waits_for_hook_locator_before_tool_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)
    attempts = 0
    real_resolve = runtime_module.resolve_data_root

    def delayed(codex_home: str, plugin_root: str) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise DataLocatorError("plugin_data_locator_unavailable")
        return real_resolve(codex_home, plugin_root)

    monkeypatch.setattr(runtime_module, "resolve_data_root", delayed)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    output = io.StringIO()
    assert (
        mcp.serve(
            stdin=io.StringIO(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
            ),
            stdout=output,
        )
        == 0
    )
    assert attempts == 3
    assert runtime.data_root() == data
    assert json.loads(output.getvalue())["result"]["tools"]
