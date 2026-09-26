"""Private Codex connection settings for the bundled HTTP transport."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from musubi_harness.plugin_runtime import RuntimeConfig, RuntimeConfigError

from musubi_codex.prompt_stage import store_prompt
from musubi_codex.runtime import CodexRuntime
from musubi_codex.stop import main as stop_main


def _runtime(tmp_path: Path) -> CodexRuntime:
    return CodexRuntime("musubi-codex", default_data_root=tmp_path)


def _config() -> RuntimeConfig:
    return RuntimeConfig(actor="proof", presence="proof/test", zone="home")


def _write(path: Path, value: object, mode: int = 0o600) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(mode)


def test_bundled_client_gets_only_the_private_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime, "memory_data_bin", lambda _config: "/venv/bin/musubi-memory-data")
    monkeypatch.setenv("MUSUBI_API_URL", "https://ambient.invalid")
    monkeypatch.setenv("MUSUBI_TOKEN", "ambient-secret")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN", "alias-secret")
    _write(
        tmp_path / "connection.json", {"api_url": "https://musubi.example", "token": "file-secret"}
    )

    env = runtime._codex_tool_environment(_config())

    assert env["MUSUBI_API_URL"] == "https://musubi.example"
    assert env["MUSUBI_TOKEN"] == "file-secret"
    assert "CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN" not in env
    assert env["FLEET_IDENTITY"] == "proof"


def test_bundled_client_does_not_inherit_ambient_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime, "memory_data_bin", lambda _config: "musubi-memory-data")
    monkeypatch.setenv("MUSUBI_API_URL", "https://ambient.invalid")
    monkeypatch.setenv("MUSUBI_TOKEN", "ambient-secret")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN", "alias-secret")

    env = runtime._codex_tool_environment(_config())

    assert "MUSUBI_API_URL" not in env
    assert "MUSUBI_TOKEN" not in env
    assert "CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN" not in env


def test_operator_transport_keeps_its_existing_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime, "memory_data_bin", lambda _config: "/fleet/bin/memory-data")
    monkeypatch.setenv("MUSUBI_API_URL", "https://operator.example")
    monkeypatch.setenv("MUSUBI_TOKEN", "operator-secret")

    env = runtime._codex_tool_environment(_config())

    assert env["MUSUBI_API_URL"] == "https://operator.example"
    assert env["MUSUBI_TOKEN"] == "operator-secret"


def test_local_stage_does_not_receive_connection_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    monkeypatch.setenv("MUSUBI_API_URL", "https://ambient.invalid")
    monkeypatch.setenv("MUSUBI_TOKEN", "ambient-secret")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN", "alias-secret")
    _write(
        tmp_path / "connection.json", {"api_url": "https://musubi.example", "token": "file-secret"}
    )

    env = runtime.local_tool_environment(_config())

    assert "MUSUBI_API_URL" not in env
    assert "MUSUBI_TOKEN" not in env
    assert "CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN" not in env


def test_stop_subprocesses_scope_connection_to_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "actor": "proof",
                "presence": "proof/test",
                "zone": "home",
                "delivery_mode": "verified",
                "memory_data_bin": "musubi-memory-data",
            }
        ),
        encoding="utf-8",
    )
    _write(
        tmp_path / "connection.json", {"api_url": "https://musubi.example", "token": "file-secret"}
    )
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("MUSUBI_API_URL", "https://ambient.invalid")
    monkeypatch.setenv("MUSUBI_TOKEN", "ambient-secret")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN", "alias-secret")
    for name in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("musubi_codex.stop.harness_bin", lambda *_args: "musubi-harness")
    monkeypatch.setattr("musubi_codex.stop.memory_data_bin", lambda *_args: "musubi-memory-data")
    submitted = {"session_id": "s", "turn_id": "t", "prompt": "Remember the proof"}
    store_prompt(submitted)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({"session_id": "s", "turn_id": "t", "last_assistant_message": "Done"})
        ),
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        calls.append((argv, env))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("musubi_codex.stop.subprocess.run", record)

    assert stop_main() == 0
    assert len(calls) == 3
    assert [
        next(action for action in ("enqueue", "stage", "drain") if action in argv)
        for argv, _env in calls
    ] == ["enqueue", "stage", "drain"]
    for _argv, env in calls[:2]:
        assert "MUSUBI_API_URL" not in env
        assert "MUSUBI_TOKEN" not in env
        assert "CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN" not in env
    assert calls[2][1]["MUSUBI_API_URL"] == "https://musubi.example"
    assert calls[2][1]["MUSUBI_TOKEN"] == "file-secret"
    assert "CLAUDE_PLUGIN_OPTION_MUSUBI_TOKEN" not in calls[2][1]


@pytest.mark.parametrize(
    "value",
    [
        {"api_url": "https://musubi.example"},
        {"api_url": "https://musubi.example", "token": "secret", "extra": "bad"},
        {"api_url": "https://musubi.example", "token": ""},
    ],
)
def test_invalid_connection_shape_is_refused_without_echoing_values(
    tmp_path: Path, value: object
) -> None:
    runtime = _runtime(tmp_path)
    _write(tmp_path / "connection.json", value)

    with pytest.raises(RuntimeConfigError, match=r"^connection_config_invalid$") as caught:
        runtime._connection()

    assert "secret" not in str(caught.value)


def test_world_readable_connection_is_refused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    _write(
        tmp_path / "connection.json",
        {"api_url": "https://musubi.example", "token": "secret"},
        0o644,
    )

    with pytest.raises(RuntimeConfigError, match=r"^connection_config_permissions_invalid$"):
        runtime._connection()


def test_symlink_connection_is_refused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    target = tmp_path / "target.json"
    _write(target, {"api_url": "https://musubi.example", "token": "secret"})
    (tmp_path / "connection.json").symlink_to(target)

    with pytest.raises(RuntimeConfigError, match=r"^connection_config_invalid$"):
        runtime._connection()
