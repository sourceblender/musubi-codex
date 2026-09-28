"""Codex Stop uses bounded batch delivery and preserves older harness compatibility."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from musubi_harness.plugin_runtime import RuntimeConfig

from musubi_codex import stop


def _run_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    drain_results: list[tuple[int, str]],
) -> tuple[list[tuple[list[str], int, dict[str, str]]], list[str]]:
    config = RuntimeConfig(
        actor="tama", presence="tama/codex", zone="home", delivery_mode="verified"
    )
    monkeypatch.setattr(stop, "runtime_config", lambda: config)
    monkeypatch.setattr(stop, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(stop, "harness_bin", lambda *_args: "musubi-harness")
    monkeypatch.setattr(stop, "memory_data_bin", lambda *_args: "musubi-memory-data")
    monkeypatch.setattr(stop, "local_tool_environment", lambda _config: {"LOCAL": "1"})
    monkeypatch.setattr(stop, "tool_environment", lambda _config: {"MUSUBI_TOKEN": "synthetic"})
    monkeypatch.setattr(stop, "clear_prompt", lambda _hook: None)
    monkeypatch.setattr(
        stop,
        "build_envelopes",
        lambda _hook: [{"actor": "tama", "zone": "home", "event_id": "one"}],
    )
    degraded: list[str] = []
    monkeypatch.setattr(stop, "_record_degraded", degraded.append)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"s"}'))
    observed: list[tuple[list[str], int, dict[str, str]]] = []
    drains = iter(drain_results)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        observed.append((command, int(kwargs["timeout"]), environment))
        if "drain" in command:
            code, stderr = next(drains)
            return subprocess.CompletedProcess(command, code, "", stderr)
        return subprocess.CompletedProcess(command, 0, '{"ok":true}', "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert stop.main() == 0
    assert capsys.readouterr().out == "{}\n"
    return observed, degraded


def test_new_harness_drains_batch_with_network_credentials_only_at_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed, degraded = _run_stop(tmp_path, monkeypatch, capsys, drain_results=[(0, "")])
    assert not degraded and len(observed) == 3
    enqueue, stage, drain = observed
    assert enqueue[2] == stage[2] == {"LOCAL": "1"}
    assert drain[2] == {"MUSUBI_TOKEN": "synthetic"}
    assert drain[0][-4:] == ["--max", "5", "--budget-seconds", "3"]
    assert drain[1] == 24
    hooks = json.loads((Path(__file__).resolve().parents[1] / "hooks" / "hooks.json").read_text())
    assert hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"] > sum(row[1] for row in observed)


def test_old_harness_retries_only_the_original_one_row_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed, degraded = _run_stop(
        tmp_path,
        monkeypatch,
        capsys,
        drain_results=[
            (2, "musubi-harness: error: unrecognized arguments: --max 5 --budget-seconds 3"),
            (0, ""),
        ],
    )
    assert not degraded and len(observed) == 4
    assert observed[-1][0] == observed[-2][0][:-4]
    assert observed[-1][1] == 15
    assert observed[-1][2] == observed[-2][2]
    hooks = json.loads((Path(__file__).resolve().parents[1] / "hooks" / "hooks.json").read_text())
    assert hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"] > sum(row[1] for row in observed)


def test_embedded_argparse_phrase_does_not_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed, degraded = _run_stop(
        tmp_path,
        monkeypatch,
        capsys,
        drain_results=[
            (
                2,
                "backend failed: musubi-harness: error: unrecognized arguments: --max 5 --budget-seconds 3; delivery may have started",
            ),
        ],
    )
    assert len(observed) == 3
    assert degraded == ["verified_delivery_failed"]


def test_other_delivery_failure_never_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    observed, degraded = _run_stop(
        tmp_path,
        monkeypatch,
        capsys,
        drain_results=[(2, "backend unavailable")],
    )
    assert len(observed) == 3
    assert degraded == ["verified_delivery_failed"]
