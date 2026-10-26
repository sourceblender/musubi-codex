"""Installed plugin: concurrent project seats, native hook binding, loopback Musubi.

Uses a deterministic loopback Responses fixture and no real credentials.
All plugin installs/state live in a disposable CODEX_HOME. The live installation
is untouched. Output contains only synthetic seat labels and bounded metadata.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import http.server
import itertools
import json
import os
import queue
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
CODEX = shutil.which("codex")
UV = shutil.which("uv")


def main() -> int:
    if not CODEX or not UV:
        print(json.dumps({"passed": False, "reason": "codex_or_uv_missing"}))
        return 1
    requests: list[dict[str, str]] = []
    model_tools: list[list[str]] = []
    ids = itertools.count(1)

    class Handler(http.server.BaseHTTPRequestHandler):
        def respond(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_input = self.rfile.read(length) if length else b""
            if self.path == "/model/responses":
                self.model(json.loads(raw_input))
                return
            token = self.headers.get("Authorization", "")
            seat = {"Bearer synthetic-yua": "yua", "Bearer synthetic-tama": "tama"}.get(
                token, "wrong"
            )
            requests.append({"seat": seat, "path": self.path, "method": self.command})
            payload = (
                {"status": "ok", "seat": seat} if self.path == "/v1/ops/status" else {"results": []}
            )
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def model(self, request: dict) -> None:
            model_tools.append([tool.get("name", "") for tool in request.get("tools", [])])
            inputs = request.get("input", [])
            last_user = max(
                (
                    i
                    for i, part in enumerate(inputs)
                    if part.get("type") == "message" and part.get("role") == "user"
                ),
                default=-1,
            )
            outputs = [
                item
                for item in inputs[last_user + 1 :]
                if item.get("type") == "function_call_output"
            ]
            if outputs:
                output = json.dumps(outputs[-1].get("output", ""))
                seat = "tama" if "tama" in output else "yua" if "yua" in output else "unbound"
                item = {
                    "type": "message",
                    "id": f"msg-proof-{next(ids)}",
                    "role": "assistant",
                    "phase": "final_answer",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": seat, "annotations": []}],
                }
            else:
                names = [tool.get("name", "") for tool in request.get("tools", [])]
                name = next(
                    (n for n in names if n.endswith("musubi_status")),
                    "mcp__musubi-codex__musubi_status",
                )
                item = {
                    "type": "function_call",
                    "id": "fc-proof",
                    "call_id": "call-proof",
                    "name": name,
                    "arguments": "{}",
                }
                for tool in request.get("tools", []):
                    if tool.get("type") == "namespace" and any(
                        child.get("name") == "musubi_status" for child in tool.get("tools", [])
                    ):
                        item["namespace"] = tool["name"]
                        item["name"] = "musubi_status"
                        break
            response = {
                "id": "resp-proof",
                "object": "response",
                "model": "proof",
                "status": "completed",
                "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            events = [
                (
                    "response.created",
                    {"response": {**response, "status": "in_progress", "output": []}},
                ),
                (
                    "response.output_item.added",
                    {
                        "output_index": 0,
                        "item": {**item, "arguments": ""}
                        if item["type"] == "function_call"
                        else {**item, "content": []},
                    },
                ),
            ]
            if item["type"] == "function_call":
                events.append(
                    (
                        "response.function_call_arguments.delta",
                        {"output_index": 0, "item_id": item["id"], "delta": "{}"},
                    )
                )
            else:
                events.append(
                    (
                        "response.output_text.delta",
                        {
                            "output_index": 0,
                            "content_index": 0,
                            "item_id": item["id"],
                            "delta": item["content"][0]["text"],
                        },
                    )
                )
            events.extend(
                [
                    ("response.output_item.done", {"output_index": 0, "item": item}),
                    ("response.completed", {"response": response}),
                ]
            )
            body = "".join(
                f"event: {kind}\ndata: {json.dumps({'type': kind, **payload})}\n\n"
                for kind, payload in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = respond

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="musubi-project-host-") as temporary:
            root = Path(temporary)
            home, bins = root / "home", root / "bin"
            home.mkdir(mode=0o700)
            bins.mkdir()
            # No production plugin, shell identity, secret, or command-chair env.
            env = {
                "HOME": str(root),
                "USER": "proof",
                "TMPDIR": tempfile.gettempdir(),
                "CODEX_HOME": str(home),
                "UV_TOOL_DIR": str(root / "tools"),
                "UV_TOOL_BIN_DIR": str(bins),
                "PATH": f"{bins}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            }
            projects = {seat: root / seat for seat in ("yua", "tama")}
            trace_script = root / "hook_trace.py"
            trace_script.write_text(
                "import json,sys\nh=json.load(sys.stdin)\nwith open(sys.argv[1], 'a') as f:f.write(json.dumps({k:h[k] for k in h if k in ('cwd','session_id','hook_event_name')})+'\\n')\n"
            )
            config = f'model="proof"\nmodel_provider="proof"\n[model_providers.proof]\nname="proof"\nbase_url="http://127.0.0.1:{server.server_address[1]}/model"\nwire_api="responses"\nrequires_openai_auth=false\n[features]\nmemories=false\nhooks=true\n'
            for seat, project in projects.items():
                folder = project / ".codex"
                folder.mkdir(parents=True)
                (folder / "musubi.toml").write_text(
                    f'schema_version=1\nactor="{seat}"\npresence="{seat}/codex"\nzone="home"\nenv_file=".env"\n'
                )
                secret = folder / ".env"
                secret.write_text(
                    f"MUSUBI_API_URL=http://127.0.0.1:{server.server_address[1]}\nMUSUBI_TOKEN=synthetic-{seat}\n"
                )
                secret.chmod(0o600)
                (folder / "hooks.json").write_text(
                    json.dumps(
                        {
                            "hooks": {
                                event: [
                                    {
                                        "hooks": [
                                            {
                                                "type": "command",
                                                "command": f'/usr/bin/python3 "{trace_script}" "{root / (seat + ".hooks")}"',
                                            }
                                        ]
                                    }
                                ]
                                for event in ("SessionStart", "UserPromptSubmit", "Stop")
                            }
                        }
                    )
                )
                config += f'[projects."{project}"]\ntrust_level="trusted"\n'
            (home / "config.toml").write_text(config)
            for command in (
                [UV, "tool", "install", "--no-cache", "--from", str(SOURCE), "musubi-codex-plugin"],
                [CODEX, "plugin", "marketplace", "add", str(SOURCE)],
                [CODEX, "plugin", "add", "musubi-codex@musubi-codex"],
            ):
                result = subprocess.run(
                    command, env=env, capture_output=True, text=True, timeout=60
                )
                if result.returncode:
                    print(
                        json.dumps(
                            {
                                "passed": False,
                                "reason": "isolated_install_failed",
                                "command": command[:3],
                            }
                        )
                    )
                    return 1

            def run(seat: str) -> dict[str, object]:
                result = subprocess.run(
                    [
                        CODEX,
                        "exec",
                        "--json",
                        "--skip-git-repo-check",
                        "--dangerously-bypass-hook-trust",
                        "-C",
                        str(projects[seat]),
                        "Call musubi_status exactly once, then answer with the seat returned by that tool. Do not call musubi_bind_context yourself, do not use a shell, and do not write memory. The plugin hook supplies context automatically.",
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=150,
                )
                events = []
                for line in result.stdout.splitlines():
                    with suppress(ValueError):
                        events.append(json.loads(line))
                tools = [
                    e["item"]
                    for e in events
                    if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "mcp_tool_call"
                ]
                # Keep failure diagnostics in disposable test storage, never the report.
                (root / f"{seat}.stdout").write_text(result.stdout)
                (root / f"{seat}.stderr").write_text(result.stderr)
                return {
                    "exit_zero": result.returncode == 0,
                    "status_called": any(t.get("tool") == "musubi_status" for t in tools),
                    "binding_called_by_model": any(
                        t.get("tool") == "musubi_bind_context" for t in tools
                    ),
                    "successful_status": any(
                        t.get("tool") == "musubi_status"
                        and not t.get("error")
                        and seat in json.dumps(t.get("result"))
                        for t in tools
                    ),
                }

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                sessions = dict(zip(projects, pool.map(run, projects), strict=True))
            # Desktop clients use the App Server API. Check a direct MCP call,
            # rather than assuming its route is identical to a CLI model call.
            api = subprocess.Popen(
                [CODEX, "app-server", "--stdio"],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            mailbox: queue.Queue[dict] = queue.Queue()

            def read_api() -> None:
                assert api.stdout is not None
                for line in api.stdout:
                    with suppress(ValueError):
                        mailbox.put(json.loads(line))

            threading.Thread(target=read_api, daemon=True).start()
            sequence = 0

            def request(method: str, params: dict) -> dict:
                nonlocal sequence
                sequence += 1
                assert api.stdin is not None
                api.stdin.write(
                    json.dumps({"id": sequence, "method": method, "params": params}) + "\n"
                )
                api.stdin.flush()
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    response = mailbox.get(timeout=max(0.1, deadline - time.monotonic()))
                    if response.get("id") == sequence:
                        return response
                raise TimeoutError(method)

            def api_turn(thread_id: str) -> None:
                request(
                    "turn/start",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": "Check the synthetic status."}],
                    },
                )
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    notification = mailbox.get(timeout=max(0.1, deadline - time.monotonic()))
                    if notification.get("method") == "turn/completed":
                        return
                raise TimeoutError("desktop turn")

            def api_status(thread_id: str) -> dict:
                return request(
                    "mcpServer/tool/call",
                    {
                        "threadId": thread_id,
                        "server": "musubi-codex",
                        "tool": "musubi_status",
                        "arguments": {},
                    },
                )

            try:
                request(
                    "initialize",
                    {
                        "clientInfo": {"name": "project-proof", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                assert api.stdin is not None
                api.stdin.write('{"method":"initialized"}\n')
                api.stdin.flush()
                listed = request("hooks/list", {"cwds": [str(projects["yua"])]})
                trusted = {}
                for entry in listed.get("result", {}).get("data", []):
                    for hook in entry["hooks"]:
                        assert hook.get("pluginId") == "musubi-codex@musubi-codex" or str(
                            trace_script
                        ) in hook.get("command", "")
                        trusted[hook["key"]] = {"trusted_hash": hook["currentHash"]}
                assert trusted
                written = request(
                    "config/batchWrite",
                    {
                        "edits": [
                            {"keyPath": "hooks.state", "value": trusted, "mergeStrategy": "upsert"}
                        ],
                        "reloadUserConfig": True,
                    },
                )
                assert "error" not in written
                started = request(
                    "thread/start",
                    {
                        "cwd": str(projects["yua"]),
                        "ephemeral": False,
                    },
                )
                thread_id = started.get("result", {}).get("thread", {}).get("id")
                request("mcpServerStatus/list", {"threadId": thread_id})
                desktop = request(
                    "mcpServer/tool/call",
                    {
                        "threadId": thread_id,
                        "server": "musubi-codex",
                        "tool": "musubi_status",
                        "arguments": {},
                    },
                )
                unbound_refused = desktop.get("result", {}).get("isError") is True
                explicit = request(
                    "mcpServer/tool/call",
                    {
                        "threadId": thread_id,
                        "server": "musubi-codex",
                        "tool": "musubi_bind_context",
                        "arguments": {
                            "cwd": str(projects["yua"]),
                            "session_id": started["result"]["thread"]["sessionId"],
                        },
                    },
                )
                explicit_status = api_status(thread_id)
                explicit_ok = (
                    not explicit.get("result", {}).get("isError")
                    and not explicit_status.get("result", {}).get("isError")
                    and "yua" in json.dumps(explicit_status)
                )
                api_turn(thread_id)
                desktop = api_status(thread_id)
                desktop_ok = "yua" in json.dumps(desktop.get("result", {})) and not desktop.get(
                    "result", {}
                ).get("isError")
                resumed = request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
                assert "error" not in resumed
                api_turn(thread_id)
                resumed_status = api_status(thread_id)
                resume_ok = not resumed_status.get("result", {}).get(
                    "isError"
                ) and "yua" in json.dumps(resumed_status)
                forked = request(
                    "thread/fork",
                    {"threadId": thread_id, "cwd": str(projects["tama"]), "excludeTurns": True},
                )
                fork_id = forked.get("result", {}).get("thread", {}).get("id")
                assert fork_id
                api_turn(fork_id)
                fork_status = api_status(fork_id)
                fork_ok = not fork_status.get("result", {}).get("isError") and "tama" in json.dumps(
                    fork_status
                )
            finally:
                api.terminate()
                api.wait(timeout=5)
            # Stop may race the transcript's final flush. Exercise the same
            # bounded recovery command that the next native SessionStart runs.
            data = home / "plugins/data/musubi-codex-musubi-codex"
            recovery = []
            for project in projects.values():
                recovered = subprocess.run(
                    [str(bins / "musubi-codex-stop"), "--drain-only"],
                    input=json.dumps({"cwd": str(project)}),
                    env={**env, "PLUGIN_DATA": str(data)},
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                recovery.append(recovered.returncode == 0)
            databases = []
            for db in (home / "plugins/data").glob("*/projects/*/*/*/shadow.db"):
                with sqlite3.connect(db) as connection:
                    count = connection.execute("SELECT count(*) FROM capture_events").fetchone()[0]
                databases.append({"actor": db.parent.parent.name, "rows": count})
            checks = {
                "native_context_binding": all(
                    v["exit_zero"]
                    and v["status_called"]
                    and v["successful_status"]
                    and not v["binding_called_by_model"]
                    for v in sessions.values()
                ),
                "two_scoped_shadow_databases": sorted((d["actor"], d["rows"]) for d in databases)
                == [("tama", 2), ("yua", 3)],
                "only_owned_loopback_requests": all(
                    r["seat"] in projects and r["path"] in ("/v1/retrieve", "/v1/ops/status")
                    for r in requests
                ),
                "expected_status_requests": sorted(
                    r["seat"] for r in requests if r["path"] == "/v1/ops/status"
                )
                == ["tama"] * 3 + ["yua"] * 6,
                "bounded_startup_recovery": all(recovery),
                "desktop_api_direct_call": bool(desktop_ok),
                "desktop_unbound_call_refused": unbound_refused,
                "desktop_explicit_client_binding": explicit_ok,
                "desktop_resume": resume_ok,
                "desktop_fork_to_other_project": fork_ok,
            }
            report = {
                "codex_version": subprocess.run(
                    [CODEX, "--version"], capture_output=True, text=True
                ).stdout.strip(),
                "source_sha256": hashlib.sha256(
                    b"".join(
                        p.read_bytes()
                        for p in sorted(
                            [
                                *SOURCE.glob("musubi_codex/*.py"),
                                SOURCE / "hooks/hooks.json",
                                SOURCE / ".mcp.json",
                            ]
                        )
                    )
                ).hexdigest(),
                "scope": "installed plugin, concurrent native CLI model calls and hooks, isolated home, loopback-only Musubi",
                "checks": checks,
                "sessions": sessions,
                "databases": databases,
                "requests": requests,
                "passed": all(checks.values()),
            }
            if not report["passed"]:
                report["desktop_response"] = desktop
                report["data_files"] = [
                    str(p.relative_to(home))
                    for p in (home / "plugins/data").rglob("*")
                    if p.is_file()
                ]
                report["degraded_reasons"] = [
                    json.loads(line).get("reason")
                    for p in (home / "plugins/data").rglob("degraded.jsonl")
                    for line in p.read_text().splitlines()
                ]
                # A caller can request private local diagnostics while debugging.
                output = os.environ.get("MUSUBI_PROOF_DEBUG_DIR")
                if output:
                    folder = Path(output)
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "model-tools.json").write_text(json.dumps(model_tools))
                    shutil.copytree(home / "plugins/data", folder / "data", dirs_exist_ok=True)
                    for seat in projects:
                        trace = root / f"{seat}.hooks"
                        if trace.exists():
                            shutil.copyfile(trace, folder / trace.name)
                        for suffix in ("stdout", "stderr"):
                            shutil.copyfile(root / f"{seat}.{suffix}", folder / f"{seat}.{suffix}")
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["passed"] else 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
