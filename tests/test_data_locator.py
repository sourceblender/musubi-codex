"""Installed Codex hook/MCP data-root handoff and refusal boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_mcp_startup_refuses_missing_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home, root, _data = _installation(tmp_path)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.chdir(root)
    assert mcp.main() == 2
    assert capsys.readouterr().err.strip() == "plugin_data_locator_unavailable"


def test_mcp_startup_binds_hook_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, root, data = _installation(tmp_path)
    publish_data_root(str(home), str(root), str(data))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.delenv("PLUGIN_DATA", raising=False)
    monkeypatch.chdir(root)
    monkeypatch.setattr(mcp._facade, "serve", lambda: 0)
    monkeypatch.setattr(runtime, "default_data_root", tmp_path / "wrong-fallback")
    assert mcp.main() == 0
    assert runtime.data_root() == data
