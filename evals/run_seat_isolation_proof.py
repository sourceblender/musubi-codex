"""Frozen two-seat, one-plugin-data-root boundary probe.

Run with ``uv run python evals/run_seat_isolation_proof.py``. All credentials,
URLs, and server names here are synthetic. The child process runs the actual
Codex adapter with the same PLUGIN_DATA for Yua and Tama.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHILD = r"""
import json
import os
from musubi_codex import prompt_recall
from musubi_codex.runtime import CodexRuntime

result = {}
runtime = CodexRuntime("musubi-codex")
try:
    config = runtime.runtime_config()
    result["actor"] = config.actor
    result["presence"] = config.presence
    result["delivery_mode"] = config.delivery_mode
    result["harness_name"] = os.path.basename(runtime.harness_bin(config))
    result["memory_data_name"] = os.path.basename(runtime.memory_data_bin(config))
    remote = runtime.tool_environment(config)
    local = runtime.local_tool_environment(config)
    result["remote_uses_seat_token"] = remote.get("MUSUBI_TOKEN") == os.environ.get("MUSUBI_TOKEN")
    result["remote_uses_seat_url"] = remote.get("MUSUBI_API_URL") == os.environ.get("MUSUBI_API_URL")
    result["local_has_no_transport"] = not ({"MUSUBI_API_URL", "MUSUBI_TOKEN"} & set(local))
    result["recall_enabled"] = prompt_recall._enabled()
except Exception as exc:
    result["error"] = str(exc)
print(json.dumps(result, sort_keys=True))
"""


def _run(root: Path, overrides: dict[str, str | None]) -> dict[str, object]:
    env = os.environ.copy()
    for name in (
        "MUSUBI_ACTOR",
        "MUSUBI_PRESENCE",
        "MUSUBI_ZONE",
        "MUSUBI_API_URL",
        "MUSUBI_TOKEN",
        "MUSUBI_HARNESS_BIN",
        "MUSUBI_MEMORY_DATA_BIN",
        "MUSUBI_DELIVERY_MODE",
        "MUSUBI_PROMPT_RECALL",
    ):
        env.pop(name, None)
    env["PLUGIN_DATA"] = str(root)
    for name, value in overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    completed = subprocess.run(
        [sys.executable, "-c", CHILD],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
        env=env,
    )
    if completed.returncode != 0:
        return {"child_failed": True}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"child_output_invalid": True}


def _seat(
    actor: str, *, delivery: str | None = None, recall: str | None = None
) -> dict[str, str | None]:
    return {
        "MUSUBI_ACTOR": actor,
        "MUSUBI_PRESENCE": f"{actor}/codex",
        "MUSUBI_ZONE": "home",
        "MUSUBI_API_URL": f"https://{actor}.invalid",
        "MUSUBI_TOKEN": f"synthetic-{actor}-token",
        "MUSUBI_DELIVERY_MODE": delivery,
        "MUSUBI_PROMPT_RECALL": recall,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-seat-isolation-") as directory:
        root = Path(directory)
        shared = {
            "actor": "other",
            "presence": "other/codex",
            "zone": "home",
            "delivery_mode": "verified",
            "harness_bin": "/legacy/musubi-harness",
            "memory_data_bin": "/legacy/memory-data",
        }
        (root / "config.json").write_text(json.dumps(shared), encoding="utf-8")
        (root / "connection.json").write_text(
            json.dumps({"api_url": "https://shared.invalid", "token": "synthetic-shared-token"}),
            encoding="utf-8",
        )
        (root / "connection.json").chmod(0o600)
        (root / "prompt_recall.json").write_text('{"enabled":true}', encoding="utf-8")
        (root / "prompt_recall.json").chmod(0o600)

        yua = _run(root, _seat("yua"))
        tama = _run(root, _seat("tama", delivery="verified", recall="true"))

        # A stale shared config must not decide a launcher seat's identity or
        # block that seat's session. The exact same payload is run again.
        (root / "config.json").write_text("{malformed", encoding="utf-8")
        stale_root = _run(root, _seat("yua"))
        (root / "config.json").write_text(json.dumps(shared), encoding="utf-8")
        partial_identity = _run(root, {"MUSUBI_ACTOR": "tama"})
        partial_transport = _seat("tama")
        partial_transport["MUSUBI_TOKEN"] = None
        partial_connection = _run(root, partial_transport)

    checks = {
        "yua_identity": yua.get("actor") == "yua" and yua.get("presence") == "yua/codex",
        "yua_shadow": yua.get("delivery_mode") == "shadow",
        "yua_recall_off": yua.get("recall_enabled") is False,
        "yua_own_transport": yua.get("remote_uses_seat_token") is True
        and yua.get("remote_uses_seat_url") is True,
        "yua_local_scrub": yua.get("local_has_no_transport") is True,
        "yua_public_bins": yua.get("harness_name") == "musubi-harness"
        and yua.get("memory_data_name") == "musubi-memory-data",
        "tama_identity": tama.get("actor") == "tama" and tama.get("presence") == "tama/codex",
        "tama_verified": tama.get("delivery_mode") == "verified",
        "tama_recall_on": tama.get("recall_enabled") is True,
        "tama_own_transport": tama.get("remote_uses_seat_token") is True
        and tama.get("remote_uses_seat_url") is True,
        "tama_local_scrub": tama.get("local_has_no_transport") is True,
        "tama_public_bins": tama.get("harness_name") == "musubi-harness"
        and tama.get("memory_data_name") == "musubi-memory-data",
        "stale_root_ignored": stale_root.get("actor") == "yua"
        and stale_root.get("delivery_mode") == "shadow",
        "partial_identity_refused": partial_identity.get("error")
        == "partial_identity_config_refused",
        "partial_transport_refused": partial_connection.get("error")
        == "connection_config_incomplete",
    }
    receipt = {
        "schema_version": 1,
        "scope": "Codex adapter subprocesses sharing one synthetic PLUGIN_DATA; no Codex host or live Musubi",
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
