"""Exercise the frozen Codex hook stream in an isolated shadow state root."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("MUSUBI_CODEX_PROOF_ROOT", Path(__file__).resolve().parents[1]))
FIXTURE = Path(__file__).resolve().parent / "fixtures/native-hook-stream-v1.json"
MANIFEST = ROOT / "hooks/hooks.json"
REQUIRED = {"UserPromptSubmit", "Stop", "Interrupt"}


def _command(hooks: dict[str, Any], event: str) -> list[str] | None:
    definitions = hooks.get(event)
    if not isinstance(definitions, list) or len(definitions) != 1:
        return None
    commands = definitions[0].get("hooks")
    if not isinstance(commands, list) or len(commands) != 1:
        return None
    raw = commands[0].get("command")
    if not isinstance(raw, str) or not raw:
        return None
    return shlex.split(raw)


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["hooks"]
    missing = sorted(REQUIRED - set(manifest))
    result: dict[str, Any] = {
        "schema": "musubi-codex-native-hook-proof-result-v1",
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "missing_hooks": missing,
        "hook_results": [],
    }
    fixture_texts = {
        value
        for event in fixture["events"]
        for key in ("prompt", "last_assistant_message")
        if isinstance(value := event.get(key), str) and value
    }

    with tempfile.TemporaryDirectory(prefix="musubi-codex-hook-proof-") as scratch:
        state = Path(scratch)
        (state / "config.json").write_text(
            json.dumps(
                {
                    "actor": "proof",
                    "presence": "proof/test",
                    "zone": "home",
                    "delivery_mode": "shadow",
                }
            ),
            encoding="utf-8",
        )
        env = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": str(state),
            "PLUGIN_DATA": str(state),
            "CODEX_HOME": str(state / "codex-home"),
        }

        for event in fixture["events"]:
            name = event["hook_event_name"]
            command = _command(manifest, name)
            if command is None:
                result["hook_results"].append({"event": name, "status": "missing"})
                continue
            if shutil.which(command[0], path=env.get("PATH")) is None:
                result["hook_results"].append({"event": name, "status": "command_unavailable"})
                continue
            completed = subprocess.run(
                command,
                input=json.dumps(event),
                text=True,
                capture_output=True,
                timeout=35,
                check=False,
                env=env,
            )
            output = completed.stdout.strip()
            result["hook_results"].append(
                {
                    "event": name,
                    "exit_code": completed.returncode,
                    "stdout_valid_json": not output or _valid_json(output),
                    "stop_output_contract": name != "Stop" or output == "{}",
                    "text_leaked": any(
                        text in completed.stdout or text in completed.stderr
                        for text in fixture_texts
                    ),
                }
            )

        db = state / "proof/home/shadow.db"
        if db.exists():
            inspected = subprocess.run(
                ["musubi-harness", "--db", str(db), "inspect", "--limit", "20"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
                env=env,
            )
            if inspected.returncode == 0:
                rows = json.loads(inspected.stdout)["result"]
            else:
                rows = []
                result["inspect_error"] = inspected.returncode
        else:
            rows = []
        result["shadow_event_count"] = len(rows)
        result["shadow_events"] = [row["envelope"] for row in rows]
        result["degradation_reasons"] = _degradation_reasons(state)
        stage = state / "proof/home/prompt-stage"
        result["pending_prompt_count"] = len(list(stage.glob("*.json"))) if stage.exists() else 0

    expected = fixture["expected"]
    matching = [
        event for event in result["shadow_events"] if event.get("event_id") == expected["event_id"]
    ]
    results = result["hook_results"]
    result["passed"] = (
        not missing
        and all(
            row.get("exit_code") == 0
            and row.get("stdout_valid_json")
            and row.get("stop_output_contract")
            and not row.get("text_leaked")
            for row in results
        )
        and len(rows) == expected["shadow_event_count"]
        and result["pending_prompt_count"] == 0
        and len(matching) == 1
        and all(
            matching[0].get(key) == expected[key]
            for key in ("user_text", "assistant_text", "actor", "zone")
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


def _valid_json(value: str) -> bool:
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return False
    return True


def _degradation_reasons(state: Path) -> list[str]:
    path = state / "degraded.jsonl"
    if not path.exists():
        return []
    return [json.loads(line)["reason"] for line in path.read_text().splitlines()]


if __name__ == "__main__":
    sys.exit(main())
