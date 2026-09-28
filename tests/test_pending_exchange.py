"""Stop write races and transient enqueue failures retain Codex exchanges."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from test_exchange_identity import answer, context, user

from musubi_codex import stop
from musubi_codex.pending import Candidate, save


@pytest.fixture(autouse=True)
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.json").write_text(json.dumps({
        "actor": "yua", "presence": "yua/command-chair", "zone": "home", "delivery_mode": "shadow",
    }))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    for key in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE"):
        monkeypatch.delenv(key, raising=False)


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def hook(path: Path, turn: str = "target") -> dict[str, str]:
    return {"session_id": "session", "turn_id": turn, "transcript_path": str(path)}


def test_stop_before_final_is_pending_then_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "transcript.jsonl", [context("target"), user("msg-u1", "Question")])
    monkeypatch.setattr(stop.time, "sleep", lambda _: None)
    seen: list[str] = []
    monkeypatch.setattr(stop, "_enqueue", lambda env, _config: seen.append(env["event_id"]))
    configured = stop.runtime_config()
    assert stop._capture_current(hook(path), configured) == []
    [candidate_path] = list((tmp_path / "yua" / "home" / "pending-exchange").glob("*.json"))
    assert candidate_path.stat().st_mode & 0o777 == 0o600
    write(path, [context("target"), user("msg-u1", "Question"), answer("msg-a1", "Answer")])
    captured = stop.drain_pending(configured)
    assert [item["event_id"] for item in captured] == ["exchange.v1:codex:session:msg-a1"]
    assert seen == ["exchange.v1:codex:session:msg-a1"]
    assert not candidate_path.exists()


def test_enqueue_failure_keeps_candidate_for_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "transcript.jsonl", [
        context("target"), user("msg-u1", "Question"), answer("msg-a1", "Answer"),
    ])
    config = stop.runtime_config()
    monkeypatch.setattr(stop, "_enqueue", lambda _env, _config: (_ for _ in ()).throw(stop.AdapterError("shadow_enqueue_failed")))
    with pytest.raises(stop.AdapterError, match="shadow_enqueue_failed"):
        stop._capture_current(hook(path), config)
    [candidate_path] = list((tmp_path / "yua" / "home" / "pending-exchange").glob("*.json"))
    seen: list[str] = []
    monkeypatch.setattr(stop, "_enqueue", lambda env, _config: seen.append(env["event_id"]))
    assert len(stop.drain_pending(config)) == 1
    assert seen == ["exchange.v1:codex:session:msg-a1"]
    assert not candidate_path.exists()


def test_candidate_exists_before_enqueue_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "transcript.jsonl", [
        context("target"), user("msg-u1", "Question"), answer("msg-a1", "Answer"),
    ])
    root = tmp_path / "yua" / "home" / "pending-exchange"

    def observe(_env: dict, _config: object) -> None:
        assert len(list(root.glob("*.json"))) == 1

    monkeypatch.setattr(stop, "_enqueue", observe)
    stop._capture_current(hook(path), stop.runtime_config())


def test_stage_failure_keeps_candidate_until_restage_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "transcript.jsonl", [
        context("target"), user("msg-u1", "Question"), answer("msg-a1", "Answer"),
    ])
    config = stop.RuntimeConfig(
        actor="yua", presence="yua/command-chair", zone="home", delivery_mode="verified"
    )
    monkeypatch.setattr(stop, "_enqueue", lambda _env, _config: None)
    captured = stop._capture_current(hook(path), config)
    [candidate_path] = list((tmp_path / "yua" / "home" / "pending-exchange").glob("*.json"))
    monkeypatch.setattr(stop, "delivery_commands", lambda env, cfg: [(["stage"], 5), (["drain"], 24)])
    monkeypatch.setattr(stop, "local_tool_environment", lambda _cfg: {})
    monkeypatch.setattr(stop, "tool_environment", lambda _cfg: {})

    def fail_stage(argv: list[str], **_kwargs: object) -> object:
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr(stop.subprocess, "run", fail_stage)
    stop._deliver(captured, config, drain_only=True)
    assert candidate_path.exists()

    def pass_stage(argv: list[str], **_kwargs: object) -> object:
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(stop.subprocess, "run", pass_stage)
    stop._deliver(captured, config, drain_only=True)
    assert not candidate_path.exists()


def test_one_pending_enqueue_failure_does_not_block_later_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = stop.runtime_config()
    root = tmp_path / "yua" / "home"
    for number in (1, 2):
        path = write(tmp_path / f"t{number}.jsonl", [
            context(f"turn-{number}"), user(f"msg-u{number}", f"Question {number}"),
            answer(f"msg-a{number}", f"Answer {number}"),
        ])
        save(root, Candidate("session", f"turn-{number}", str(path), created_at=time.time() - 3 + number))
    seen: list[str] = []

    def enqueue(env: dict, _config: object) -> None:
        if env["event_id"].endswith("msg-a1"):
            raise stop.AdapterError("shadow_enqueue_failed")
        seen.append(env["event_id"])

    monkeypatch.setattr(stop, "_enqueue", enqueue)
    captured = stop.drain_pending(config)
    assert [item["event_id"] for item in captured] == ["exchange.v1:codex:session:msg-a2"]
    assert seen == ["exchange.v1:codex:session:msg-a2"]
    remaining = list((root / "pending-exchange").glob("*.json"))
    assert len(remaining) == 1
