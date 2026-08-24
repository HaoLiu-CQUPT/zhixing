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
import re
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


_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINK_RE = re.compile(r"<think\b[^>]*>.*\Z", re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
_ROBOTIC_PREFIX_RE = re.compile(
    r"^(?:根据(?:文档|资料|知识库)(?:摘录|内容|信息)?|"
    r"从(?:文档|资料|知识库)(?:摘录|内容|信息)?来看)[，,:：]\s*"
)


def humanize_answer(value: str) -> str:
    """Return plain chat text without model reasoning or Markdown artifacts."""
    text = _THINK_BLOCK_RE.sub("", value)
    text = _UNCLOSED_THINK_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "• ", text)
    text = _ROBOTIC_PREFIX_RE.sub("", text.strip())
    text = re.sub(r"(?<=\d)\s*至\s*(?=\d)", " 至 ", text)
    text = re.sub(r"[ \t]+([，。！？；：])", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sanitize_nested_outputs(payload: dict[str, Any]) -> bool:
    """Remove reasoning from diagnostic workflow events before local logging."""
    changed = False
    data = payload.get("data")
    if not isinstance(data, dict):
        return changed
    outputs = data.get("outputs")
    if not isinstance(outputs, dict):
        return changed
    for key in ("answer", "text"):
        value = outputs.get(key)
        if isinstance(value, str):
            cleaned = humanize_answer(value)
            if cleaned != value:
                outputs[key] = cleaned
                changed = True
    reasoning = outputs.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        outputs["reasoning_content"] = ""
        changed = True
    return changed


def _parse_sse_payload(block: str) -> dict[str, Any] | None:
    data_lines = [
        line[5:].lstrip()
        for line in block.splitlines()
        if line.startswith("data:")
    ]
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    if not data or data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _render_sse_payload(block: str, payload: dict[str, Any]) -> str:
    prefix = [line for line in block.splitlines() if not line.startswith("data:")]
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return "\n".join([*prefix, f"data: {data}"])


def sanitize_sse_body(body: bytes) -> tuple[bytes, bool, int | None]:
    text = body.decode("utf-8")
    blocks = [block for block in text.replace("\r\n", "\n").split("\n\n") if block]
    parsed = [_parse_sse_payload(block) for block in blocks]

    answer = ""
    message_indices: list[int] = []
    message_template: dict[str, Any] | None = None
    for index, payload in enumerate(parsed):
        if payload is None:
            continue
        event = payload.get("event")
        fragment = payload.get("answer")
        if event in {"message", "agent_message", "message_replace"} and isinstance(
            fragment, str
        ):
            message_indices.append(index)
            if message_template is None or event != "message_replace":
                message_template = dict(payload)
            if event == "message_replace":
                answer = fragment
            elif len(fragment) > len(answer) and fragment.startswith(answer):
                answer = fragment
            else:
                answer += fragment

    if not message_indices or message_template is None:
        changed = False
        rendered: list[str] = []
        for block, payload in zip(blocks, parsed):
            if payload is not None and _sanitize_nested_outputs(payload):
                changed = True
                rendered.append(_render_sse_payload(block, payload))
            else:
                rendered.append(block)
        return ("\n\n".join(rendered) + "\n\n").encode("utf-8"), changed, None

    cleaned_answer = humanize_answer(answer)
    if not cleaned_answer:
        cleaned_answer = "暂时没有生成可用的回答，请稍后再试。"
    message_template["event"] = "message"
    message_template["answer"] = cleaned_answer
    final_message_index = message_indices[-1]

    rendered = []
    changed = cleaned_answer != answer
    for index, (block, payload) in enumerate(zip(blocks, parsed)):
        if index in message_indices:
            if index == final_message_index:
                rendered.append(_render_sse_payload(block, message_template))
            continue
        if payload is not None and _sanitize_nested_outputs(payload):
            changed = True
            rendered.append(_render_sse_payload(block, payload))
        else:
            rendered.append(block)
    return (
        ("\n\n".join(rendered) + "\n\n").encode("utf-8"),
        changed,
        len(cleaned_answer),
    )


def sanitize_upstream_body(
    content_type: str, body: bytes
) -> tuple[bytes, bool, int | None]:
    if "text/event-stream" in content_type.lower() or body.lstrip().startswith(b"data:"):
        try:
            return sanitize_sse_body(body)
        except (UnicodeDecodeError, ValueError):
            return body, False, None

    if "json" not in content_type.lower():
        return body, False, None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, False, None
    if not isinstance(payload, dict):
        return body, False, None

    changed = _sanitize_nested_outputs(payload)
    answer = payload.get("answer")
    answer_chars: int | None = None
    if isinstance(answer, str):
        cleaned = humanize_answer(answer)
        if not cleaned:
            cleaned = "暂时没有生成可用的回答，请稍后再试。"
        answer_chars = len(cleaned)
        if cleaned != answer:
            payload["answer"] = cleaned
            changed = True
    if not changed:
        return body, False, answer_chars
    sanitized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return sanitized, True, answer_chars


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
            content_type = response.getheader("Content-Type", "application/octet-stream")
            response_body, response_sanitized, answer_chars = sanitize_upstream_body(
                content_type, response_body
            )
            duration_ms = round((time.monotonic() - started) * 1000)

            self.send_response(response.status, response.reason)
            self.send_header(
                "Content-Type",
                content_type,
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
                response_sanitized=response_sanitized,
                answer_chars=answer_chars,
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
