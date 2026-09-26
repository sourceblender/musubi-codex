"""Exercise the installed UserPromptSubmit executable with a fake Musubi API."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "prompt-recall-v1.json"


def _jwt() -> str:
    def part(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join(
        (
            part({"alg": "none"}),
            part({"iss": "synthetic-proof", "sub": "tama/codex", "presence": "tama/codex", "scope": "tama/codex/episodic:r"}),
            "synthetic-signature",
        )
    )


def main() -> int:
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    requests: list[dict[str, object]] = []
    token = _jwt()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            requests.append({"path": self.path, "body": json.loads(body), "authorization": self.headers.get("Authorization")})
            raw = json.dumps(fixture["cases"]["conflicting_ranked_decisions"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="codex-installed-prompt-proof-") as temporary:
            data = Path(temporary)
            for name, value in (
                ("config.json", {**fixture["identity"], "delivery_mode": "shadow"}),
                ("connection.json", {"api_url": f"http://127.0.0.1:{server.server_address[1]}", "token": token}),
                ("prompt_recall.json", {"enabled": True}),
            ):
                path = data / name
                path.write_text(json.dumps(value), encoding="utf-8")
                path.chmod(0o600)
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in {
                    "MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE",
                    "MUSUBI_DELIVERY_MODE", "MUSUBI_API_URL", "MUSUBI_TOKEN",
                    "MUSUBI_MEMORY_DATA_BIN", "MUSUBI_HARNESS_BIN",
                }
            }
            env["PLUGIN_DATA"] = str(data)
            payload = {"session_id": "installed-prompt-proof", "turn_id": "one", "prompt": fixture["prompt"]}
            completed = subprocess.run(
                ["musubi-codex-user-prompt-submit"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=8,
                check=False,
                env=env,
            )
            output = json.loads(completed.stdout)
            context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            checks = {
                "installed_command_resolved": shutil.which("musubi-codex-user-prompt-submit") is not None,
                "bundled_client_resolved": shutil.which("musubi-memory-data") is not None and shutil.which("memory-data") is None,
                "hook_exited_zero": completed.returncode == 0,
                "one_request": len(requests) == 1,
                "exact_endpoint": bool(requests) and requests[0]["path"] == "/v1/retrieve",
                "exact_namespace": bool(requests) and requests[0]["body"].get("namespace") == fixture["namespace"],
                "settled_filter": bool(requests) and requests[0]["body"].get("state_filter") == ["matured", "promoted"],
                "token_to_only_local_server": bool(requests) and requests[0]["authorization"] == "Bearer " + token,
                "bounded_context": 0 < len(context) <= 1200,
                "honest_recency": "recency_score=0.100" in context and "recency_score=0.900" in context and "not a date or proof of the latest decision" in context,
                "no_token_in_hook_output": token not in completed.stdout and token not in completed.stderr,
                "stage_created": any(data.rglob("prompt-stage/*.json")),
            }
            receipt = {
                "schema_version": 1,
                "scope": "installed_UserPromptSubmit_executable_fake_HTTP",
                "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                "checks": checks,
                "request_count": len(requests),
                "context_chars": len(context),
                "passed": all(checks.values()),
                "real_Codex_host_injection": "not_run",
            }
            print(json.dumps(receipt, indent=2, sort_keys=True))
            return 0 if receipt["passed"] else 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
