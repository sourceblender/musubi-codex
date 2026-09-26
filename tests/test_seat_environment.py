"""Two Codex seats under one OS user must never share transport settings."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from musubi_harness.plugin_runtime import RuntimeConfigError

from musubi_codex import prompt_recall, session_start
from musubi_codex.runtime import CodexRuntime


def _seat(monkeypatch: pytest.MonkeyPatch, actor: str, token: str) -> None:
    monkeypatch.setenv("MUSUBI_ACTOR", actor)
    monkeypatch.setenv("MUSUBI_PRESENCE", f"{actor}/codex")
    monkeypatch.setenv("MUSUBI_ZONE", "home")
    monkeypatch.setenv("MUSUBI_API_URL", "https://musubi.example")
    monkeypatch.setenv("MUSUBI_TOKEN", token)


def _jwt(subject: str, scope: str, *, presence: str | None = None) -> str:
    def part(value: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    claims = {"sub": subject, "scope": scope, "presence": presence or subject}
    return f"{part({'alg': 'none'})}.{part(claims)}.synthetic"


def test_two_launchers_ignore_shared_file_and_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "config.json").write_text("{malformed", encoding="utf-8")
    (tmp_path / "connection.json").write_text(
        json.dumps({"api_url": "https://wrong.example", "token": "wrong-secret"}),
        encoding="utf-8",
    )
    runtime = CodexRuntime("musubi-codex", default_data_root=tmp_path)
    monkeypatch.setenv("MUSUBI_HARNESS_BIN", "/fleet-tools/bin/musubi-harness")
    monkeypatch.setenv("MUSUBI_MEMORY_DATA_BIN", "/fleet-tools/bin/memory-data")
    for actor, token in (("yua", "yua-secret"), ("tama", "tama-secret")):
        _seat(monkeypatch, actor, token)
        config = runtime.runtime_config()
        assert config.actor == actor
        assert config.presence == f"{actor}/codex"
        assert config.delivery_mode == "shadow"
        assert Path(runtime.harness_bin(config)).name == "musubi-harness"
        assert Path(runtime.memory_data_bin(config)).name == "musubi-memory-data"
        assert (
            Path(runtime.memory_data_bin(config)).parent == Path(runtime.harness_bin(config)).parent
        )
        remote = runtime.tool_environment(config)
        local = runtime.local_tool_environment(config)
        assert remote["MUSUBI_TOKEN"] == token
        assert remote["MUSUBI_API_URL"] == "https://musubi.example"
        assert "MUSUBI_TOKEN" not in local and "MUSUBI_API_URL" not in local
        assert "MUSUBI_HARNESS_BIN" not in local
        assert "MUSUBI_MEMORY_DATA_BIN" not in remote


def test_partial_identity_or_transport_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CodexRuntime("musubi-codex", default_data_root=tmp_path)
    monkeypatch.setenv("MUSUBI_ACTOR", "yua")
    with pytest.raises(RuntimeConfigError, match="partial_identity_config_refused"):
        runtime.runtime_config()
    _seat(monkeypatch, "yua", "token")
    monkeypatch.delenv("MUSUBI_TOKEN")
    with pytest.raises(RuntimeConfigError, match="connection_config_incomplete"):
        runtime.memory_data_bin(runtime.runtime_config())


def test_delivery_and_recall_are_seat_opt_ins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "prompt_recall.json").write_text('{"enabled":true}', encoding="utf-8")
    runtime = CodexRuntime("musubi-codex", default_data_root=tmp_path)
    monkeypatch.setattr(prompt_recall.runtime, "data_root", lambda: tmp_path)
    for actor in ("yua", "tama"):
        _seat(monkeypatch, actor, f"{actor}-secret")
        assert runtime.runtime_config().delivery_mode == "shadow"
        assert prompt_recall._enabled() is False
    monkeypatch.setenv("MUSUBI_DELIVERY_MODE", "verified")
    monkeypatch.setenv("MUSUBI_PROMPT_RECALL", "true")
    assert runtime.runtime_config().delivery_mode == "verified"
    assert prompt_recall._enabled() is True


def test_mcp_manifest_forwards_seat_transport() -> None:
    manifest = json.loads((Path(__file__).parents[1] / ".mcp.json").read_text())
    forwarded = set(manifest["mcpServers"]["musubi-codex"]["env_vars"])
    assert {
        "MUSUBI_ACTOR",
        "MUSUBI_PRESENCE",
        "MUSUBI_ZONE",
        "MUSUBI_API_URL",
        "MUSUBI_TOKEN",
    } <= forwarded


def test_wrong_seat_token_refuses_transport_and_warns_at_session_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt("tama/codex", "tama/codex/*:rw")
    _seat(monkeypatch, "yua", token)
    runtime = CodexRuntime("musubi-codex", default_data_root=tmp_path)
    config = runtime.runtime_config()
    with pytest.raises(RuntimeConfigError, match="token_presence_mismatch"):
        runtime.tool_environment(config)
    monkeypatch.setattr(session_start, "runtime", runtime)
    monkeypatch.setattr(
        session_start._continuity,
        "continuity_block",
        lambda: pytest.fail("wrong token must not reach remote recall or delivery"),
    )
    warning = session_start.continuity_block()
    assert "tama/codex" in warning and "yua/codex" in warning
    assert "Remote recall and delivery are refused" in warning
    assert token not in warning


def test_matching_subject_with_wrong_presence_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt("yua/codex", "yua/codex/*:rw", presence="tama/codex")
    _seat(monkeypatch, "yua", token)
    runtime = CodexRuntime("musubi-codex", default_data_root=tmp_path)
    with pytest.raises(RuntimeConfigError, match="token_presence_mismatch"):
        runtime.tool_environment(runtime.runtime_config())
