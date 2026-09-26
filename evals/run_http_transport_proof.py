"""Exercise the installed Codex adapter against a disposable Musubi HTTP service.

Run this file with the interpreter that has musubi-codex and the published
musubi-harness wheel installed. The fixture stays in this checkout; imports
come from that interpreter's installed packages, not from the checkout.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).parent / "fixtures" / "http-transport-v1.json"


def _jwt() -> str:
    def part(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return ".".join(
        (
            part({"alg": "none"}),
            part(
                {
                    "iss": "synthetic-proof",
                    "sub": "proof/test",
                    "presence": "proof/test",
                    "scope": "proof/**:rw",
                }
            ),
            "synthetic-signature",
        )
    )


class FakeMusubi:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.redirect_to: str | None = None


@contextmanager
def serve(fake: FakeMusubi) -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            fake.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            if self.path == "/v1/ops/status" and fake.redirect_to:
                status, headers, payload = 302, {"Location": fake.redirect_to}, {}
            elif self.path == "/v1/ops/status":
                status, headers, payload = 200, {}, {"status": "ok"}
            elif self.path == "/v1/retrieve":
                status, headers, payload = 200, {}, {"results": []}
            else:
                status, headers, payload = 404, {}, {"error": "unknown"}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = _handle

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _write_connection(path: Path, url: str, token: str, mode: int = 0o600) -> None:
    path.write_text(json.dumps({"api_url": url, "token": token}), encoding="utf-8")
    path.chmod(mode)


def main() -> int:
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    token = _jwt()
    checks: dict[str, bool] = {}
    request_counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="musubi-codex-http-proof-") as directory:
        root = Path(directory)
        data = root / "plugin-data"
        data.mkdir(mode=0o700)
        config = data / "config.json"
        config.write_text(
            json.dumps(
                {
                    "actor": fixture["actor"],
                    "presence": fixture["presence"],
                    "zone": fixture["zone"],
                    "delivery_mode": "shadow",
                }
            ),
            encoding="utf-8",
        )
        config.chmod(0o600)
        connection = data / "connection.json"

        # The process running this gate must have only the installed wheel's
        # console scripts on PATH. Ambient credentials are deliberate decoys.
        os.environ["PLUGIN_DATA"] = str(data)
        os.environ["MUSUBI_TOKEN"] = "ambient.decoy.sig"
        os.environ.pop("MUSUBI_MEMORY_DATA_BIN", None)
        os.environ.pop("MUSUBI_HARNESS_BIN", None)
        for name in ("MUSUBI_ACTOR", "MUSUBI_PRESENCE", "MUSUBI_ZONE", "MUSUBI_DELIVERY_MODE"):
            os.environ.pop(name, None)

        from musubi_codex.mcp import call_tool  # imported after isolated env is set
        from musubi_codex.runtime import runtime
        from musubi_codex.session_start import continuity_block

        configured = runtime.runtime_config()
        selected = runtime.memory_data_bin(configured)
        checks["bundled_selected"] = Path(selected).name == "musubi-memory-data"
        checks["no_private_operator"] = shutil.which("memory-data") is None
        checks["installed_commands"] = all(
            shutil.which(name) is not None
            for name in ("musubi-codex-mcp", "musubi-codex-session-start", "musubi-memory-data")
        )

        first = FakeMusubi()
        with serve(first) as url:
            # The ambient endpoint is deliberately reachable. A missing file
            # must not silently fall back to it, and an installed client that
            # prefers it over the private file leaves a visible request.
            os.environ["MUSUBI_API_URL"] = url
            _write_connection(connection, url, token)
            status = call_tool(configured, "musubi_status", {})
            continuity = continuity_block()
            configured_count = len(first.requests)
            connection.unlink()
            missing = call_tool(configured, "musubi_status", {})
        request_counts["configured"] = configured_count
        request_counts["after_missing"] = len(first.requests)
        checks["status"] = status.get("structuredContent", {}).get("result", {}).get("status") == "ok"
        checks["private_connection_used"] = bool(first.requests) and all(
            request["authorization"] == f"Bearer {token}" for request in first.requests
        )
        checks["continuity_empty_distinct"] = "no matches" in continuity.lower() and "unavailable" not in continuity.lower()
        emitted = json.dumps(status) + continuity
        checks["no_token_in_output"] = token not in emitted and "ambient.decoy.sig" not in emitted
        checks["missing_unavailable"] = missing.get("isError") is True and "unavailable" in json.dumps(missing)
        checks["missing_no_ambient_fallback"] = len(first.requests) == configured_count

        guarded = FakeMusubi()
        with serve(guarded) as url:
            _write_connection(connection, url, token, 0o644)
            insecure = call_tool(configured, "musubi_status", {})
        request_counts["insecure"] = len(guarded.requests)
        checks["insecure_refused"] = insecure.get("isError") is True and not guarded.requests

        connection.unlink()
        malformed = FakeMusubi()
        with serve(malformed) as url:
            os.environ["MUSUBI_API_URL"] = url
            connection.write_text('{"api_url":', encoding="utf-8")
            connection.chmod(0o600)
            malformed_result = call_tool(configured, "musubi_status", {})
        request_counts["malformed"] = len(malformed.requests)
        checks["malformed_refused"] = malformed_result.get("isError") is True and not malformed.requests
        connection.unlink()

        target = root / "target.json"
        _write_connection(target, "http://127.0.0.1:9", token)
        connection.symlink_to(target)
        symlinked = call_tool(configured, "musubi_status", {})
        checks["symlink_refused"] = symlinked.get("isError") is True
        connection.unlink()

        other = FakeMusubi()
        with serve(other) as other_url:
            redirect = FakeMusubi()
            redirect.redirect_to = other_url + "/v1/ops/status"
            with serve(redirect) as url:
                _write_connection(connection, url, token)
                refused = call_tool(configured, "musubi_status", {})
        request_counts["redirect_origin"] = len(redirect.requests)
        request_counts["redirect_target"] = len(other.requests)
        checks["redirect_refused"] = refused.get("isError") is True and bool(redirect.requests) and not other.requests
        checks["no_token_in_errors"] = all(
            token not in json.dumps(value) and "ambient.decoy.sig" not in json.dumps(value)
            for value in (missing, insecure, malformed_result, symlinked, refused)
        )

    receipt = {
        "schema_version": 1,
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "python": sys.version.split()[0],
        "checks": checks,
        "request_counts": request_counts,
        "passed": all(checks.values()),
        "scope": "disposable_http_boundary_only; real_Musubi_readback_pending",
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
