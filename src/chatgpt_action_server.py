#!/usr/bin/env python3
"""Zero-dependency REST bridge for ChatGPT GPT Actions.

The existing Latent Memory server speaks MCP over stdio. ChatGPT Plus cannot
attach that local process directly, but a private custom GPT can call HTTPS
Actions described by an OpenAPI schema. This bridge keeps the memory core and
storage format unchanged and only translates HTTP requests into the existing
MCP tool calls.

Secrets are read from environment variables only. Put TLS in front of this
process (or run it behind a hosting platform's HTTPS proxy) in production.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 256 * 1024


def _default_runtime_src() -> Path:
    configured = os.environ.get("LATENT_MEMORY_SRC")
    if configured:
        return Path(configured).expanduser().resolve()
    # In the published repository this bridge lives beside mcp_server.py.
    return Path(__file__).resolve().parent


def build_memory_server(
    runtime_src: Path,
    corpus: Path,
    threads: Path | None,
    *,
    embed: bool = False,
    embed_provider: str | None = None,
):
    """Load the existing memory core without copying business logic here."""
    runtime_src = runtime_src.resolve()
    if not (runtime_src / "mcp_server.py").is_file():
        raise RuntimeError(
            f"Latent Memory runtime not found at {runtime_src}. "
            "Pass --runtime-src or set LATENT_MEMORY_SRC."
        )
    sys.path.insert(0, str(runtime_src))

    from embedding_provider import resolve_provider
    from mcp_server import MemoryServer
    from memory_retrieval import load_corpus
    from session_thread import ThreadStore

    corpus = corpus.resolve()
    if not corpus.is_dir():
        raise RuntimeError(
            f"Memory corpus directory does not exist: {corpus}. "
            "Refusing to start with a silently empty memory store."
        )
    provider = resolve_provider(embed_provider) if embed else None
    index = load_corpus(str(corpus), embed=embed, provider=provider)
    return MemoryServer(
        index=index,
        thread_store=ThreadStore(str(threads.resolve()) if threads else None),
        corpus_dir=str(corpus),
        weights_path=corpus / ".weights.json",
        retractions_path=corpus / ".retractions.json",
        entities_path=corpus / ".entities.json",
    )


class ActionGateway:
    """Thread-safe adapter from REST operations to the existing MCP dispatcher."""

    def __init__(self, memory_server):
        self.memory_server = memory_server
        self._lock = threading.RLock()
        self._next_id = 1

    def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            response = self.memory_server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                }
            )

        if not response:
            raise RuntimeError("Memory server returned no response")
        if "error" in response:
            raise ValueError(response["error"].get("message", "Memory protocol error"))
        result = response.get("result") or {}
        content = result.get("content") or []
        text = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ).strip()
        if result.get("isError"):
            raise ValueError(text or "Memory tool failed")
        return text


def _schema_for(base_url: str) -> dict[str, Any]:
    error_schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "error": {"type": "string"},
        },
        "required": ["ok", "error"],
    }
    result_schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "result": {"type": "string"},
        },
        "required": ["ok", "result"],
    }
    json_response = {
        "200": {
            "description": "Memory operation completed",
            "content": {"application/json": {"schema": result_schema}},
        },
        "401": {
            "description": "Missing or invalid API key",
            "content": {"application/json": {"schema": error_schema}},
        },
        "422": {
            "description": "Memory operation was understood but could not be completed",
            "content": {"application/json": {"schema": error_schema}},
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Private Long-Term Memory",
            "description": (
                "Private cross-conversation memory for one user. Search before claiming "
                "a past detail is unknown; save important events immediately."
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/v1/session/start": {
                "get": {
                    "operationId": "startMemorySession",
                    "summary": "Recall the previous conversation state",
                    "description": (
                        "Call once at the beginning of every new conversation, before the "
                        "first substantive reply. Returns recent context and open loops."
                    ),
                    "responses": json_response,
                }
            },
            "/v1/memory/search": {
                "get": {
                    "operationId": "searchLongTermMemory",
                    "summary": "Search long-term memory",
                    "description": (
                        "Call before answering about past events, promises, dates, places, "
                        "names, preferences, or any detail that may be uncertain."
                    ),
                    "parameters": [
                        {
                            "name": "query",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                            "description": "Natural-language description of what to recall",
                        },
                        {
                            "name": "topN",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        },
                    ],
                    "responses": json_response,
                }
            },
            "/v1/memory/append": {
                "post": {
                    "operationId": "appendLongTermMemory",
                    "summary": "Save an important event",
                    "description": (
                        "Save new agreements, important events, state changes, or anything the "
                        "user explicitly asks to remember. Call immediately rather than waiting "
                        "for the conversation to end."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string", "minLength": 1},
                                        "current_state": {"type": "string", "minLength": 1},
                                        "window": {"type": "integer", "minimum": 1},
                                    },
                                    "required": ["text", "current_state"],
                                }
                            }
                        },
                    },
                    "responses": json_response,
                }
            },
            "/v1/memory/correct": {
                "post": {
                    "operationId": "correctLongTermMemory",
                    "summary": "Retract and optionally replace an incorrect memory",
                    "description": (
                        "First search memory, then copy an exact unique quote from the result. "
                        "Use this when the user says a stored detail is wrong or outdated."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "quote": {"type": "string", "minLength": 1},
                                        "reason": {"type": "string", "minLength": 1},
                                        "correction": {"type": "string"},
                                        "current_state": {"type": "string"},
                                    },
                                    "required": ["quote", "reason"],
                                }
                            }
                        },
                    },
                    "responses": json_response,
                }
            },
            "/v1/thread/close": {
                "post": {
                    "operationId": "closeMemorySession",
                    "summary": "Save a conversation checkpoint",
                    "description": (
                        "Save topics, current state, and open loops when the user clearly ends "
                        "the conversation. Do not postpone important memories until this call."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "window": {"type": "integer", "minimum": 1},
                                        "current_state": {"type": "string", "minLength": 1},
                                        "topics": {"type": "array", "items": {"type": "string"}},
                                        "open_loops": {"type": "array", "items": {"type": "string"}},
                                        "started_at": {"type": "number"},
                                    },
                                    "required": ["window", "current_state"],
                                }
                            }
                        },
                    },
                    "responses": json_response,
                }
            },
        },
    }


class MemoryActionHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address, handler_class, *, gateway, api_key, public_base_url=None):
        super().__init__(server_address, handler_class)
        self.gateway = gateway
        self.api_key = api_key
        self.public_base_url = public_base_url


class ActionRequestHandler(BaseHTTPRequestHandler):
    server: MemoryActionHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        # Never log request bodies or Authorization headers.
        sys.stderr.write("memory-actions: " + (fmt % args) + "\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.api_key}"
        return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))

    def _require_auth(self) -> bool:
        if self._authorized():
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Unauthorized"})
        return False

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length"})
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length > MAX_BODY_BYTES else HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "JSON body is required and must be at most 256 KiB"},
            )
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Body must be valid UTF-8 JSON"})
            return None
        if not isinstance(payload, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "JSON body must be an object"})
            return None
        return payload

    def _base_url(self) -> str:
        if self.server.public_base_url:
            return self.server.public_base_url.rstrip("/")
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip()
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host")
        return f"{proto}://{host}".rstrip("/")

    def _call(self, tool: str, args: dict[str, Any]) -> None:
        try:
            result = self.server.gateway.call(tool, args)
        except ValueError as exc:
            self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"ok": False, "error": str(exc)})
            return
        except Exception:
            self.log_error("unexpected memory server failure")
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Internal server error"})
            return
        self._send_json(HTTPStatus.OK, {"ok": True, "result": result})

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True, "status": "ready"})
            return
        if parsed.path == "/openapi.json":
            self._send_json(HTTPStatus.OK, _schema_for(self._base_url()))
            return
        if parsed.path == "/privacy":
            data = (
                "Private memory service. It stores only data explicitly sent by its owner and "
                "does not sell or share it. Contact the service owner for deletion requests."
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if not self._require_auth():
            return
        query = parse_qs(parsed.query)
        if parsed.path == "/v1/session/start":
            self._call("session_start", {})
            return
        if parsed.path == "/v1/memory/search":
            args: dict[str, Any] = {"query": (query.get("query") or [""])[0]}
            if "topN" in query:
                try:
                    args["topN"] = int(query["topN"][0])
                except (TypeError, ValueError):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "topN must be an integer"})
                    return
                if not 1 <= args["topN"] <= 20:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "topN must be between 1 and 20"})
                    return
            self._call("memory_search", args)
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        routes = {
            "/v1/memory/append": "memory_append",
            "/v1/memory/correct": "memory_correct",
            "/v1/thread/close": "thread_close",
        }
        tool = routes.get(parsed.path)
        if tool is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return
        if not self._require_auth():
            return
        payload = self._read_json()
        if payload is not None:
            self._call(tool, payload)


def create_http_server(
    gateway: ActionGateway,
    api_key: str,
    host: str,
    port: int,
    public_base_url: str | None = None,
) -> MemoryActionHTTPServer:
    if len(api_key) < 24:
        raise ValueError("MEMORY_ACTION_API_KEY must contain at least 24 characters")
    return MemoryActionHTTPServer(
        (host, port),
        ActionRequestHandler,
        gateway=gateway,
        api_key=api_key,
        public_base_url=public_base_url,
    )


def _selftest(runtime_src: Path) -> None:
    import tempfile
    import urllib.error
    import urllib.parse
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        corpus = root / "memory"
        timeline = corpus / "timeline"
        timeline.mkdir(parents=True)
        (timeline / "window_01_2026-08-03.md").write_text(
            "# 测试窗口\n\n她把蓝色玻璃珠放进木盒。\n\n"
            "**当下状态：** 玻璃珠仍在木盒里。\n",
            encoding="utf-8",
        )
        memory_server = build_memory_server(runtime_src, corpus, root / "threads.jsonl")
        gateway = ActionGateway(memory_server)
        api_key = "selftest-key-with-more-than-24-chars"
        httpd = create_http_server(gateway, api_key, "127.0.0.1", 0)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"

        def request(path: str, *, method="GET", body=None, auth=True):
            headers = {}
            if auth:
                headers["Authorization"] = f"Bearer {api_key}"
            data = None
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))

        try:
            status, schema = request("/openapi.json", auth=False)
            assert status == 200 and schema["openapi"] == "3.1.0"
            try:
                request("/v1/session/start", auth=False)
                raise AssertionError("unauthorized call should fail")
            except urllib.error.HTTPError as exc:
                assert exc.code == 401

            q = urllib.parse.quote("蓝色玻璃珠 木盒")
            status, found = request(f"/v1/memory/search?query={q}")
            assert status == 200 and "玻璃珠" in found["result"]

            status, appended = request(
                "/v1/memory/append",
                method="POST",
                body={"text": "她决定把测试桥叫作小门。", "current_state": "名称已经确定。"},
            )
            assert status == 200 and appended["ok"] is True
            q2 = urllib.parse.quote("测试桥 小门")
            status, found2 = request(f"/v1/memory/search?query={q2}")
            assert status == 200 and "小门" in found2["result"]
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    print("chatgpt_action_server selftest ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose Latent Memory as private ChatGPT GPT Actions")
    parser.add_argument("--runtime-src", type=Path, default=_default_runtime_src())
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--threads", type=Path)
    parser.add_argument("--host", default=os.environ.get("HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--public-base-url", default=os.environ.get("MEMORY_ACTION_BASE_URL"))
    parser.add_argument("--embed", action="store_true")
    parser.add_argument("--embed-provider")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest(args.runtime_src)
        return
    if args.corpus is None:
        parser.error("--corpus is required unless --selftest is used")
    api_key = os.environ.get("MEMORY_ACTION_API_KEY", "")
    if not api_key:
        parser.error("MEMORY_ACTION_API_KEY is required")

    memory_server = build_memory_server(
        args.runtime_src,
        args.corpus,
        args.threads,
        embed=args.embed,
        embed_provider=args.embed_provider,
    )
    httpd = create_http_server(
        ActionGateway(memory_server),
        api_key,
        args.host,
        args.port,
        public_base_url=args.public_base_url,
    )
    print(f"Memory Actions listening on http://{args.host}:{httpd.server_port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
