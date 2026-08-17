"""Loopback-only Dify Cloud proxy for the reviewed WeCom test kernel.

The proxy deliberately does not log prompts, answers, API keys, or raw user
identifiers. It records only operational metadata needed to diagnose the test
pipeline.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = int(os.environ.get("DIFY_ADAPTER_PORT", "8765"))
UPSTREAM_HOST = "api.dify.ai"
MAX_REQUEST_BYTES = 5 * 1024 * 1024
ALLOWED_PATHS = {
    "/v1/chat-messages",
    "/v1/completion-messages",
    "/v1/workflows/run",
}

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
LOG_PATH = RUNTIME_DIR / "adapter-events.jsonl"

_log_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "started_at": None,
    "request_count": 0,
    "last_request_at": None,
    "last_status": None,
    "last_duration_ms": None,
    "last_path": None,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def append_event(event: str, **fields: Any) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": utc_now(), "event": event, **fields}
    line = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    with _log_lock:
        with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def hash_user(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(normalized).hexdigest()[:12]


def request_metadata(body: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "request_bytes": len(body),
        "query_length": None,
        "user_hash": None,
        "response_mode": None,
    }
    try:
        payload = json.loads(body.decode("utf-8"))
        if isinstance(payload, dict):
            query = payload.get("query")
            metadata["query_length"] = len(query) if isinstance(query, str) else None
            metadata["user_hash"] = hash_user(payload.get("user"))
            mode = payload.get("response_mode")
            metadata["response_mode"] = mode if isinstance(mode, str) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    return metadata


class AdapterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ZhixingDifyAdapter/1.0"

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/health":
            self.send_json(404, {"error": "not_found"})
            return
        with _state_lock:
            snapshot = dict(_state)
        self.send_json(
            200,
            {
                "service": "wecom-dify-adapter",
                "status": "ok",
                "listen": f"{HOST}:{PORT}",
                **snapshot,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path not in ALLOWED_PATHS:
            self.send_json(404, {"error": "not_found"})
            return

        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) <= len(
            "Bearer "
        ):
            self.send_json(401, {"error": "missing_bearer_token"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "invalid_content_length"})
            return
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self.send_json(413, {"error": "invalid_request_size"})
            return

        body = self.rfile.read(content_length)
        metadata = request_metadata(body)
        started = time.monotonic()
        append_event("request_received", path=self.path, **metadata)

        upstream = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=120)
        try:
            upstream.request(
                "POST",
                self.path,
                body=body,
                headers={
                    "Authorization": authorization,
                    "Content-Type": self.headers.get(
                        "Content-Type", "application/json"
                    ),
                    "Accept": self.headers.get("Accept", "*/*"),
                    "User-Agent": "Zhixing-WeCom-Dify-Adapter/1.0",
                },
            )
            response = upstream.getresponse()
            response_body = response.read()
            duration_ms = round((time.monotonic() - started) * 1000)

            self.send_response(response.status, response.reason)
            self.send_header(
                "Content-Type",
                response.getheader("Content-Type", "application/octet-stream"),
            )
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)

            with _state_lock:
                _state["request_count"] += 1
                _state["last_request_at"] = utc_now()
                _state["last_status"] = response.status
                _state["last_duration_ms"] = duration_ms
                _state["last_path"] = self.path
            append_event(
                "upstream_response",
                path=self.path,
                status=response.status,
                response_bytes=len(response_body),
                duration_ms=duration_ms,
            )
        except Exception as exc:  # network boundary: convert to a controlled 502
            duration_ms = round((time.monotonic() - started) * 1000)
            with _state_lock:
                _state["request_count"] += 1
                _state["last_request_at"] = utc_now()
                _state["last_status"] = 502
                _state["last_duration_ms"] = duration_ms
                _state["last_path"] = self.path
            append_event(
                "upstream_error",
                path=self.path,
                error_type=type(exc).__name__,
                duration_ms=duration_ms,
            )
            self.send_json(502, {"error": "upstream_unavailable"})
        finally:
            upstream.close()


def main() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with _state_lock:
        _state["started_at"] = utc_now()
    append_event("adapter_started", host=HOST, port=PORT)
    server = ThreadingHTTPServer((HOST, PORT), AdapterHandler)
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
