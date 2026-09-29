#!/usr/bin/env python3
"""Manual installed Codex host probe with two synthetic seats.

Set MUSUBI_CODE_ROOT to the checkout being reviewed and run this script in a
Codex-authenticated local environment. It creates a disposable CODEX_HOME,
installs the checkout and its PyPI dependencies, calls a loopback fake Musubi
server, and prints only a redacted receipt. It makes no live Musubi writes.
"""

import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import tomllib
from contextlib import suppress
from pathlib import Path

SOURCE = Path(os.environ.get("MUSUBI_CODE_ROOT", Path.cwd())).resolve()
PROJECT_VERSION = tomllib.loads((SOURCE / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]
CODEX = shutil.which("codex")
UV = shutil.which("uv")
AUTH = Path(os.environ.get("CODEX_AUTH_PATH", Path.home() / ".codex" / "auth.json")).resolve()


def run(args, env, *, timeout=60):
    return subprocess.run(
        args, env=env, text=True, capture_output=True, timeout=timeout, check=False
    )


def main():
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization", "")
            seat = {"Bearer synthetic-yua-token": "yua", "Bearer synthetic-tama-token": "tama"}.get(
                auth, "other"
            )
            requests.append({"method": "GET", "path": self.path, "seat": seat})
            raw = json.dumps({"ok": True, "seat": seat}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            auth = self.headers.get("Authorization", "")
            seat = {"Bearer synthetic-yua-token": "yua", "Bearer synthetic-tama-token": "tama"}.get(
                auth, "other"
            )
            requests.append({"method": "POST", "path": self.path, "seat": seat})
            self.send_error(405)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="tama-codex-two-seat-host-") as temporary:
            root = Path(temporary)
            home = root / "codex-home"
            home.mkdir(mode=0o700)
            (home / "auth.json").symlink_to(AUTH)
            bin_dir = root / "tool-bin"
            tool_dir = root / "tool-dir"
            bin_dir.mkdir()
            tool_dir.mkdir()
            base = os.environ.copy()
            for name in list(base):
                if name.startswith("MUSUBI_") or name.startswith("FLEET_"):
                    base.pop(name)
            base.update(
                {
                    "CODEX_HOME": str(home),
                    "UV_TOOL_DIR": str(tool_dir),
                    "UV_TOOL_BIN_DIR": str(bin_dir),
                    "PATH": f"{bin_dir}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                }
            )
            setup = {}
            for label, command in (
                (
                    "python_tool",
                    [
                        UV,
                        "tool",
                        "install",
                        "--no-cache",
                        "--python",
                        "3.12",
                        "--from",
                        str(SOURCE),
                        "musubi-codex-plugin",
                    ],
                ),
                ("marketplace", [CODEX, "plugin", "marketplace", "add", str(SOURCE)]),
                ("plugin", [CODEX, "plugin", "add", "musubi-codex@musubi-codex"]),
            ):
                completed = run(command, base)
                setup[label] = completed.returncode == 0
                if completed.returncode:
                    print(
                        json.dumps(
                            {
                                "scope": "setup",
                                "setup": setup,
                                "failed_step": label,
                                "detail": completed.stderr[-500:],
                            }
                        )
                    )
                    return 1

            sessions = {}
            for seat in ("yua", "tama"):
                project = root / f"{seat}-vault"
                project.mkdir()
                env = base | {
                    "MUSUBI_ACTOR": seat,
                    "MUSUBI_PRESENCE": f"{seat}/codex",
                    "MUSUBI_ZONE": "home",
                    "MUSUBI_API_URL": f"http://127.0.0.1:{server.server_address[1]}",
                    "MUSUBI_TOKEN": f"synthetic-{seat}-token",
                    "MUSUBI_DELIVERY_MODE": "shadow",
                    "MUSUBI_PROMPT_RECALL": "false",
                    "MUSUBI_HARNESS_BIN": "/legacy/musubi-harness",
                    "MUSUBI_MEMORY_DATA_BIN": "/legacy/memory-data",
                }
                prompt = "Call the musubi_status MCP tool exactly once. Then answer with the seat value returned by the tool. Do not use shell commands or write memory."
                completed = run(
                    [
                        CODEX,
                        "exec",
                        "--json",
                        "--ephemeral",
                        "--skip-git-repo-check",
                        "--dangerously-bypass-hook-trust",
                        "-C",
                        str(project),
                        prompt,
                    ],
                    env,
                    timeout=120,
                )
                events = []
                for line in completed.stdout.splitlines():
                    with suppress(json.JSONDecodeError):
                        events.append(json.loads(line))
                calls = [
                    event.get("item", {})
                    for event in events
                    if event.get("type") == "item.completed"
                    and event.get("item", {}).get("type") == "mcp_tool_call"
                ]
                sessions[seat] = {
                    "exit_zero": completed.returncode == 0,
                    "mcp_calls": len(calls),
                    "called_status": any(
                        "musubi_status" in str(item.get("tool", "")) for item in calls
                    ),
                    "request_seats": [item["seat"] for item in requests if item["seat"] == seat],
                    "error_events": sum(event.get("type") == "error" for event in events),
                }
            tool_python = next(tool_dir.glob("*/bin/python"))
            versions = run(
                [
                    str(tool_python),
                    "-c",
                    'import importlib.metadata as m; print(m.version("musubi-codex-plugin")); print(m.version("musubi-harness"))',
                ],
                base,
            ).stdout.splitlines()
            data_roots = sorted(
                path.name for path in (home / "plugins" / "data").glob("*musubi-codex*")
            )
            receipt = {
                "schema_version": 1,
                "scope": "installed Codex plugin and local Python tool, two seat sessions sharing one CODEX_HOME, fake loopback HTTP only",
                "plugin_head": run(
                    ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], base
                ).stdout.strip(),
                "codex_version": run([CODEX, "--version"], base).stdout.strip(),
                "python_plugin_version": versions[0] if len(versions) == 2 else "unknown",
                "harness_version": versions[1] if len(versions) == 2 else "unknown",
                "setup": setup,
                "sessions": sessions,
                "requests": requests,
                "plugin_data_roots": data_roots,
            }
            receipt["passed"] = (
                all(setup.values())
                and all(
                    sessions[seat]["exit_zero"]
                    and sessions[seat]["mcp_calls"] >= 1
                    and sessions[seat]["called_status"]
                    for seat in ("yua", "tama")
                )
                and [item for item in requests if item["path"] == "/v1/ops/status"]
                == [
                    {"method": "GET", "path": "/v1/ops/status", "seat": "yua"},
                    {"method": "GET", "path": "/v1/ops/status", "seat": "tama"},
                ]
                and all(
                    item["path"] in ("/v1/retrieve", "/v1/ops/status")
                    and item["seat"] in ("yua", "tama")
                    for item in requests
                )
                and data_roots == [".musubi-codex-locators", "musubi-codex-musubi-codex"]
                and receipt["python_plugin_version"] == PROJECT_VERSION
                and tuple(int(part) for part in receipt["harness_version"].split(".")[:3])
                >= (1, 7, 0)
            )
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0 if receipt["passed"] else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
