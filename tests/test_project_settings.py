"""Project settings remain separate from launcher and single-seat deployments."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from musubi_harness.plugin_runtime import RuntimeConfigError

from musubi_codex import interrupt, mcp, prompt_recall, session_start, stop, user_prompt_submit
from musubi_codex.pending import Candidate, load_all, save
from musubi_codex.project import discover, load
from musubi_codex.runtime import CodexRuntime, runtime


def project(
    root: Path,
    actor: str = "yua",
    *,
    presence: str | None = None,
    delivery: str = "shadow",
    recall: bool = False,
) -> Path:
    folder = root / ".codex"
    folder.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    path = folder / "musubi.toml"
    path.write_text(f'''schema_version = 1
actor = "{actor}"
presence = "{presence or actor + "/codex"}"
zone = "home"
env_file = ".env"
delivery_mode = "{delivery}"
prompt_recall = {str(recall).lower()}
''')
    path.chmod(0o644)
    secret = folder / ".env"
    secret.write_text(
        f"MUSUBI_API_URL=https://{actor}.invalid\nMUSUBI_TOKEN={actor}-secret\nGITHUB_TOKEN=unrelated\n"
    )
    secret.chmod(0o600)
    return path


def test_project_discovery_respects_nearest_worktree(tmp_path: Path) -> None:
    path = project(tmp_path)
    sub = tmp_path / "src" / "nested"
    sub.mkdir(parents=True)
    assert discover(str(sub)) == path
    (sub / ".git").write_text("gitdir: elsewhere")
    assert discover(str(sub)) is None
    with pytest.raises(RuntimeConfigError, match="cwd_invalid"):
        discover("relative")


@pytest.mark.parametrize(
    "replacement",
    [
        "schema_version=true",
        "schema_version=2",
        'actor="../other"',
        'presence="tama/codex"',
        'zone="elsewhere"',
        'delivery_mode="automatic"',
        'prompt_recall="true"',
        'env_file=""',
        'unknown="value"',
    ],
)
def test_invalid_settings_fail_closed(tmp_path: Path, replacement: str) -> None:
    path = project(tmp_path)
    key = replacement.split("=", 1)[0]
    lines = [line for line in path.read_text().splitlines() if not line.startswith(key + " ")]
    path.write_text("\n".join([*lines, replacement]))
    with pytest.raises(RuntimeConfigError, match="project_"):
        load(path)


def test_defaults_and_private_credentials(tmp_path: Path) -> None:
    path = project(tmp_path)
    path.write_text(
        "\n".join(
            line
            for line in path.read_text().splitlines()
            if not line.startswith(("delivery_mode", "prompt_recall"))
        )
    )
    selected = load(path)
    assert selected.config.delivery_mode == "shadow" and not selected.prompt_recall
    assert selected.connection() == ("https://yua.invalid", "yua-secret")
    selected.env_file.chmod(0o644)
    with pytest.raises(RuntimeConfigError, match="permissions_invalid"):
        selected.connection()


def test_project_config_must_not_be_writable_by_others(tmp_path: Path) -> None:
    path = project(tmp_path)
    path.chmod(0o666)
    with pytest.raises(RuntimeConfigError, match="permissions_invalid"):
        load(path)


def test_project_storage_rejects_symlink(tmp_path: Path) -> None:
    project(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (data / "projects").symlink_to(other, target_is_directory=True)
    instance = CodexRuntime("musubi-codex", default_data_root=data)
    with (
        instance.hook_context({"cwd": str(tmp_path)}),
        pytest.raises(RuntimeConfigError, match="data_permissions_invalid"),
    ):
        instance.data_root()


@pytest.mark.parametrize("target", ["config", "env"])
def test_symlinked_settings_and_secrets_refused(tmp_path: Path, target: str) -> None:
    path = project(tmp_path)
    original = path if target == "config" else path.parent / ".env"
    moved = original.with_name(original.name + ".real")
    original.rename(moved)
    original.symlink_to(moved)
    with pytest.raises(RuntimeConfigError, match="unreadable"):
        load(path).connection()


@pytest.mark.parametrize(
    "contents",
    [
        "MUSUBI_TOKEN=one\nMUSUBI_TOKEN=two",
        'MUSUBI_TOKEN="unterminated',
        "MUSUBI_TOKEN=op://vault/item/field",
        "MUSUBI_TOKEN=${OTHER_TOKEN}",
        "MUSUBI_TOKEN=one two",
        "MUSUBI_TOKEN=token\nMUSUBI_ACTOR=tama",
        "MUSUBI_TOKEN=token\nMUSUBI_DELIVERY_MODE=verified",
        "MUSUBI_API_URL=https://service.invalid",
    ],
)
def test_env_validation_without_execution(tmp_path: Path, contents: str) -> None:
    path = project(tmp_path)
    selected = load(path)
    selected.env_file.write_text(contents)
    with pytest.raises(RuntimeConfigError, match="project_"):
        selected.connection()


def test_env_values_are_data_and_errors_hide_secrets(tmp_path: Path) -> None:
    selected = load(project(tmp_path))
    marker = tmp_path / "must-not-exist"
    selected.env_file.write_text(
        f"export MUSUBI_API_URL=https://service.invalid\nMUSUBI_TOKEN='$(touch {marker}) # literal'\n"
    )
    assert selected.connection()[1] == f"$(touch {marker}) # literal"
    assert not marker.exists()
    selected.env_file.write_text('MUSUBI_TOKEN="private-broken-token')
    with pytest.raises(RuntimeConfigError) as caught:
        selected.connection()
    assert "private-broken-token" not in str(caught.value)


def test_two_projects_use_separate_credentials_and_outboxes(tmp_path: Path) -> None:
    before = dict(os.environ)
    for actor in ("yua", "tama"):
        path = project(tmp_path / actor, actor)
        instance = CodexRuntime("musubi-codex", default_data_root=tmp_path / "data")
        with instance.hook_context({"cwd": str(path.parent.parent)}):
            configured = instance.runtime_config()
            remote = instance._codex_tool_environment(configured)
            local = instance.local_tool_environment(configured)
            assert configured.actor == actor
            assert remote["MUSUBI_TOKEN"] == actor + "-secret"
            assert "MUSUBI_TOKEN" not in local and "MUSUBI_API_URL" not in local
            assert instance.data_root().parent.name == "projects"
            if actor == "yua":
                yua_root = instance.data_root()
            else:
                assert instance.data_root() != yua_root
        assert instance._project is None
    assert dict(os.environ) == before


def test_same_actor_different_policies_have_separate_storage(tmp_path: Path) -> None:
    path = project(tmp_path)
    instance = CodexRuntime("musubi-codex", default_data_root=tmp_path / "data")
    with instance.hook_context({"cwd": str(tmp_path)}):
        old = instance.data_root()
    path.write_text(path.read_text().replace('"shadow"', '"verified"'))
    with instance.hook_context({"cwd": str(tmp_path)}):
        assert instance.data_root() != old


def test_launcher_remains_authoritative_and_conflicts_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)
    instance = CodexRuntime("musubi-codex", default_data_root=tmp_path / "data")
    for key, value in {
        "MUSUBI_ACTOR": "yua",
        "MUSUBI_PRESENCE": "yua/codex",
        "MUSUBI_ZONE": "home",
        "MUSUBI_DELIVERY_MODE": "verified",
    }.items():
        monkeypatch.setenv(key, value)
    with instance.hook_context({"cwd": str(tmp_path)}):
        assert instance.runtime_config().delivery_mode == "verified"
        assert instance._project is None
    monkeypatch.setenv("MUSUBI_ACTOR", "tama")
    with (
        pytest.raises(RuntimeConfigError, match="launcher_identity_mismatch"),
        instance.hook_context({"cwd": str(tmp_path)}),
    ):
        pass
    monkeypatch.delenv("MUSUBI_ZONE")
    with (
        pytest.raises(RuntimeConfigError, match="partial_identity"),
        instance.hook_context({"cwd": str(tmp_path)}),
    ):
        pass


def test_explicit_project_path_supported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = project(tmp_path)
    monkeypatch.setenv("MUSUBI_CONFIG_FILE", str(path))
    instance = CodexRuntime("musubi-codex", default_data_root=tmp_path / "data")
    assert instance.runtime_config().actor == "yua"
    assert (
        instance._codex_tool_environment(instance.runtime_config())["MUSUBI_TOKEN"] == "yua-secret"
    )


def test_binding_is_idempotent_and_rebinding_blocks_old_context(tmp_path: Path) -> None:
    one = project(tmp_path / "one")
    two = project(tmp_path / "two", "tama")
    instance = CodexRuntime("musubi-codex")
    assert instance.bind_context(str(one.parent.parent), "s")["mode"] == "project"
    assert instance.bind_context(str(one.parent.parent), "s")["ok"]
    with pytest.raises(RuntimeConfigError, match="rebind_refused"):
        instance.bind_context(str(two.parent.parent), "other")
    with pytest.raises(RuntimeConfigError, match="rebind_refused"):
        instance.runtime_config()
    # A retry of the original valid native context may recover the connection.
    instance.bind_context(str(one.parent.parent), "s")
    assert instance.runtime_config().actor == "yua"


def test_rotation_allowed_but_policy_edit_requires_new_binding(tmp_path: Path) -> None:
    path = project(tmp_path)
    instance = CodexRuntime("musubi-codex")
    instance.bind_context(str(tmp_path), "s")
    reference = instance.project_reference()
    (path.parent / ".env").write_text(
        "MUSUBI_API_URL=https://rotated.invalid\nMUSUBI_TOKEN=rotated"
    )
    assert instance._codex_tool_environment(instance.runtime_config())["MUSUBI_TOKEN"] == "rotated"
    assert instance.project_reference() == reference
    path.write_text(path.read_text().replace('"shadow"', '"verified"'))
    with pytest.raises(RuntimeConfigError, match="context_changed"):
        instance.runtime_config()


def test_mcp_binding_bootstraps_without_identity_or_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)

    def response(request: dict, configured: object) -> dict:
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"actor": configured.actor}}

    monkeypatch.setattr(mcp._facade, "response_for", response)
    messages = [
        {"id": 1, "method": "tools/list"},
        {
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "musubi_bind_context",
                "arguments": {"cwd": str(tmp_path), "session_id": "s"},
            },
        },
        {"id": 3, "method": "tools/call", "params": {"name": "musubi_status", "arguments": {}}},
    ]
    output = io.StringIO()
    mcp.serve(stdin=[json.dumps(m) for m in messages], stdout=output)
    results = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
    assert "musubi_bind_context" in [tool["name"] for tool in results[0]["tools"]]
    assert not results[1].get("isError")
    assert results[2]["actor"] == "yua"


def test_malformed_binding_does_not_leave_old_context_usable(tmp_path: Path) -> None:
    project(tmp_path)
    messages = [
        {
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "musubi_bind_context",
                "arguments": {"cwd": str(tmp_path), "session_id": "s"},
            },
        },
        {
            "id": 2,
            "method": "tools/call",
            "params": {"name": "musubi_bind_context", "arguments": {"cwd": str(tmp_path)}},
        },
        {"id": 3, "method": "tools/call", "params": {"name": "musubi_status", "arguments": {}}},
    ]
    output = io.StringIO()
    mcp.serve(stdin=[json.dumps(m) for m in messages], stdout=output)
    results = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
    assert not results[0].get("isError")
    assert results[1]["isError"] and results[2]["isError"]


def test_pending_binding_is_nonsecret_and_legacy_records_still_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path)
    with runtime.hook_context({"cwd": str(tmp_path)}):
        candidate = stop._candidate_from_hook(
            {"session_id": "s", "turn_id": "t", "transcript_path": str(tmp_path / "t.jsonl")}
        )
        root = runtime.data_root() / "yua" / "home"
        path = save(root, candidate)
        assert load_all(root)[0][1] == candidate
        assert "yua-secret" not in path.read_text()
    legacy = Candidate("old", "turn", str(tmp_path / "old.jsonl"), time.time())
    legacy_path = save(tmp_path / "legacy", legacy)
    assert "project_config" not in json.loads(legacy_path.read_text())
    assert load_all(tmp_path / "legacy")[0][1] == legacy


def test_drain_never_reinterprets_different_project_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path)
    with runtime.hook_context({"cwd": str(tmp_path)}):
        root = runtime.data_root() / "yua" / "home"
        candidate = Candidate(
            "s",
            "t",
            str(tmp_path / "old.jsonl"),
            time.time(),
            project_config=str(tmp_path / "other.toml"),
            project_fingerprint="0" * 64,
        )
        path = save(root, candidate)
        monkeypatch.setattr(
            stop, "build_envelopes", lambda *_a, **_k: pytest.fail("must not reinterpret")
        )
        assert stop.drain_pending(runtime.runtime_config()) == []
        assert path.exists()


@pytest.mark.parametrize("entry", ["start", "prompt", "stop", "drain", "interrupt"])
def test_each_hook_enters_project_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entry: str
) -> None:
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    project(tmp_path, "tama", recall=True)
    hook = {"cwd": str(tmp_path), "session_id": "s", "turn_id": "t", "prompt": "question"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook)))
    if entry in {"stop", "interrupt"}:
        with runtime.hook_context(hook):
            runtime.record_turn(hook)
    observed = []

    def check(*_args: object, **_kwargs: object) -> None:
        observed.append(runtime.runtime_config().actor)

    if entry == "start":
        monkeypatch.setattr(session_start, "continuity_block", lambda: (check(), "ready")[1])
        session_start.main()
    elif entry == "prompt":
        monkeypatch.setattr(user_prompt_submit, "store_prompt", check)
        monkeypatch.setattr(user_prompt_submit, "context_for", lambda _prompt: None)
        user_prompt_submit.main()
    elif entry == "interrupt":
        monkeypatch.setattr(interrupt, "clear_prompt", check)
        interrupt.main()
    else:
        monkeypatch.setattr(stop, "drain_pending", lambda *_a, **_k: (check(), [])[1])
        monkeypatch.setattr(stop, "_capture_current", lambda *_a: [])
        monkeypatch.setattr(stop, "clear_prompt", lambda *_a: None)
        stop.main(["--drain-only"] if entry == "drain" else [])
    capsys.readouterr()
    assert observed == ["tama"]
    assert runtime._project is None


def test_project_prompt_recall_is_opt_in(tmp_path: Path) -> None:
    project(tmp_path, recall=False)
    with runtime.hook_context({"cwd": str(tmp_path)}):
        assert not prompt_recall._enabled()


def test_pending_survives_project_edit_during_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path)
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    with runtime.hook_context({"cwd": str(tmp_path)}):
        candidate = stop._candidate_from_hook(
            {"session_id": "s", "turn_id": "t", "transcript_path": str(tmp_path / "t.jsonl")}
        )
        path = save(runtime.data_root() / "yua/home", candidate)

        def changed(*_args: object, **_kwargs: object) -> None:
            raise stop.AdapterError("project_context_changed")

        monkeypatch.setattr(stop, "build_envelopes", changed)
        monkeypatch.setattr(stop, "_record_degraded", lambda _reason: None)
        assert stop.drain_pending(runtime.runtime_config()) == []
        assert path.exists()


def test_verified_project_scopes_db_and_scrubs_local_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project(tmp_path, delivery="verified")
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "data"))
    seen: list[tuple[list[str], dict[str, str]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")

    monkeypatch.setattr(stop.subprocess, "run", run)
    with runtime.hook_context({"cwd": str(tmp_path)}):
        configured = runtime.runtime_config()
        root = runtime.data_root()
        envelope = {"actor": "yua", "zone": "home", "event_id": "synthetic"}
        stop._enqueue(envelope, configured)
        stop._deliver([envelope], configured)
        assert len(seen) == 3
        for command, environment in seen:
            assert str(root / "yua/home/shadow.db") in command
            if "drain" in command:
                assert environment["MUSUBI_TOKEN"] == "yua-secret"
            else:
                assert "MUSUBI_TOKEN" not in environment
                assert "MUSUBI_API_URL" not in environment


def test_project_refuses_known_wrong_seat_token(tmp_path: Path) -> None:
    from test_seat_environment import _jwt

    path = project(tmp_path)
    secret = path.parent / ".env"
    token = _jwt("tama/codex", "tama/codex/*:rw")
    secret.write_text(f"MUSUBI_API_URL=https://service.invalid\nMUSUBI_TOKEN={token}\n")
    with runtime.hook_context({"cwd": str(tmp_path)}):
        with pytest.raises(RuntimeConfigError, match="token_presence_mismatch") as caught:
            runtime.tool_environment(runtime.runtime_config())
        assert token not in str(caught.value)


def test_hook_manifest_binds_only_musubi_agent_tools() -> None:
    import re

    hooks = json.loads((Path(__file__).parents[1] / "hooks/hooks.json").read_text())
    binding = hooks["hooks"]["PreToolUse"][0]
    assert re.match(binding["matcher"], "mcp__musubi_codex__musubi_status")
    assert re.match(binding["matcher"], "mcp__musubi-codex__musubi_remember")
    assert not re.match(binding["matcher"], "mcp__musubi_codex__musubi_bind_context")
    assert not re.match(binding["matcher"], "mcp__unrelated__status")
    assert binding["hooks"][0]["input"] == {
        "cwd": "${cwd}",
        "session_id": "${session_id}",
        "hook_event_name": "${hook_event_name}",
    }


@pytest.mark.parametrize("event", [None, "UserPromptSubmit", "PreToolUse"])
def test_native_binding_returns_hook_output(tmp_path: Path, event: str | None) -> None:
    project(tmp_path)
    request = {
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "musubi_bind_context",
            "arguments": {
                "cwd": str(tmp_path),
                "session_id": "s",
                **({"hook_event_name": event} if event else {}),
            },
        },
    }
    output = io.StringIO()
    mcp.serve(stdin=[json.dumps(request)], stdout=output)
    result = json.loads(output.getvalue())["result"]
    assert not result.get("isError")
    assert json.loads(result["content"][0]["text"]) == (
        {} if event else {"ok": True, "mode": "project"}
    )
    assert mcp.runtime.runtime_config().actor == "yua"
