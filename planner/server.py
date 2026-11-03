"""Local HTTP API and the signed-in session. The page is served from ui/."""

from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import socket
import threading
import time
import traceback
from pathlib import Path
from uuid import uuid4
from urllib.parse import parse_qs, urlparse

import uvicorn

from planner.alarms import AlarmBook
from planner.auth import AuthError, authorize_url, code_verifier, exchange_profile
from planner.assistant import (
    OLLAMA_CHAT_TIMEOUT,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_NUM_CTX,
    OneRetry,
    run_turn,
)
from planner.bridge import JsApi, ViewState
from planner.clock import Clock
from planner.crypto import open_local, seal_local
from planner.models import AlarmStatus, StoreError
from planner.service import PlannerService
from planner.settings import Settings
from planner.user_store import auth_profile, open_user_store

RPC_ACTIONS = {
    "snapshot",
    "set_selected_day",
    "create_task",
    "update_task",
    "delete_task",
    "add_subtask",
    "update_subtask",
    "delete_subtask",
    "add_link",
    "delete_link",
    "add_alarm",
    "delete_alarm",
    "undo",
    "redo",
    "drain_alarm_events",
    "open_link",
}

SESSION_MAX_AGE = 30 * 24 * 60 * 60
SESSION_AAD = b"planner-session"

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}


class Session:
    def __init__(self, sub: str, name: str, picture: str | None) -> None:
        self.sub = sub
        self.name = name
        self.picture = picture
        self.view = ViewState()
        self.chat_session_id = str(uuid4())
        self._chat_status = ""
        self._chat_log: list[dict] = []
        self._chat_status_lock = threading.Lock()

    def set_chat_status(self, text: str) -> None:
        with self._chat_status_lock:
            self._chat_status = text

    def read_chat_status(self) -> str:
        with self._chat_status_lock:
            return self._chat_status

    def append_chat(self, role: str, text: str) -> None:
        if role not in {"user", "assistant"} or not isinstance(text, str) or not text.strip():
            return
        with self._chat_status_lock:
            self._chat_log.append({"role": role, "text": text})
            if len(self._chat_log) > 80:
                del self._chat_log[:-80]

    def read_chat(self) -> list[dict]:
        with self._chat_status_lock:
            return [dict(item) for item in self._chat_log]


class ActiveBooks:
    """Alarm scheduler target. It ticks every signed-in user's document."""

    def __init__(self) -> None:
        self._books: list[AlarmBook] = []
        self._lock = threading.Lock()

    def add(self, book: AlarmBook) -> None:
        with self._lock:
            if book not in self._books:
                self._books.append(book)

    def transition_due_alarms(self, now, outcome: AlarmStatus) -> None:
        with self._lock:
            books = list(self._books)
        for book in books:
            book.transition_due_alarms(now, outcome)


class _Listener:
    """The bound address tests read. Uvicorn owns the socket."""

    def __init__(self, address: tuple[str, int]) -> None:
        self.server_address = address


class _Headers:
    def __init__(self, items: list[tuple[bytes, bytes]]) -> None:
        grouped: dict[str, list[str]] = {}
        for key, value in items:
            name = key.decode("latin-1").lower()
            grouped.setdefault(name, []).append(value.decode("latin-1"))
        self._items = {}
        for name, values in grouped.items():
            self._items[name] = "; ".join(values) if name == "cookie" else ", ".join(values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._items.get(key.lower(), default)


class _Exchange:
    """Request and response the route methods write, then the ASGI app sends."""

    def __init__(self, path: str, headers: _Headers, body: bytes) -> None:
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = self
        self.status = 500
        self.committed = False
        self._headers: list[tuple[str, str]] = []
        self._body = bytearray()

    def send_response(self, status: int, message: str | None = None) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self._headers.append((key, value))

    def end_headers(self) -> None:
        self.committed = True

    def write(self, data: bytes) -> None:
        self._body.extend(data)


class PlannerApp:
    def __init__(self, settings: Settings, root: Path, clock: Clock, master_key: bytes, ui_dir: Path) -> None:
        self.settings = settings
        self.root = root
        self.clock = clock
        self.master_key = master_key
        self.ui_dir = ui_dir
        self.books = ActiveBooks()
        self.sessions: dict[str, Session] = {}
        self._load_remembered()
        self._services: dict[str, PlannerService] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def serve(self, port: int | None = None) -> _Listener:
        chosen = self.settings.port if port is None else port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", chosen))
            sock.listen(128)
            address = sock.getsockname()
        except OSError:
            sock.close()
            raise
        config = uvicorn.Config(self, host=address[0], port=address[1], log_level="warning", access_log=False)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, name="planner-http", daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                sock.close()
                raise RuntimeError("Uvicorn did not start")
            time.sleep(0.01)
        self._server = server
        self._thread = thread
        return _Listener(address)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        path = scope.get("path") or "/"
        query = scope.get("query_string") or b""
        if query:
            path = path + "?" + query.decode("latin-1")
        exchange = _Exchange(path, _Headers(scope.get("headers") or []), await _read_body(receive))
        await asyncio.to_thread(self._dispatch, scope.get("method") or "GET", exchange)
        await send(
            {
                "type": "http.response.start",
                "status": exchange.status,
                "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in exchange._headers],
            }
        )
        await send({"type": "http.response.body", "body": bytes(exchange._body)})

    def sign_in_for_tests(self, sub: str, name: str = "Ada", remember: bool = False) -> str:
        session_id = secrets.token_urlsafe(18)
        session = Session(sub, name, None)
        self.sessions[session_id] = session
        if remember:
            self._remember(session_id, session)
        return session_id

    def handle_get(self, handler: _Exchange) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/login":
            self._login(handler)
            return
        if parsed.path == "/callback":
            self._callback(handler, parse_qs(parsed.query))
            return
        if parsed.path == "/api/session":
            self._session_status(handler)
            return
        if parsed.path == "/api/chat/status":
            self._chat_activity(handler)
            return
        if parsed.path in STATIC_FILES:
            self._static(handler, STATIC_FILES[parsed.path])
            return
        _send_json(handler, 404, {"ok": False, "error": "Not found"})

    def handle_post(self, handler: _Exchange) -> None:
        parsed = urlparse(handler.path)
        if parsed.path == "/logout":
            _read_json(handler)
            self._logout(handler)
            return
        session = self._session(handler)
        if session is None:
            _send_json(handler, 401, {"ok": False, "missing": self.settings.missing_auth(), "error": "Sign in required"})
            return
        body = _read_json(handler)
        if parsed.path == "/api/rpc":
            status, payload = self._rpc(session, body)
            _send_json(handler, status, payload)
            return
        if parsed.path == "/api/chat":
            status, payload = self._chat(session, body)
            _send_json(handler, status, payload)
            return
        _send_json(handler, 404, {"ok": False, "error": "Not found"})

    def service_for(self, session: Session) -> PlannerService:
        profile = auth_profile(session.sub, session.name, session.picture)
        with self._lock:
            service = self._services.get(session.sub)
            fresh = service is None
            if service is None:
                store = open_user_store(self.root, profile, self.master_key, self.clock, self.settings)
                service = PlannerService(store, self.clock)
                self._services[session.sub] = service
        if fresh:
            service.transition_due_alarms(self.clock.now(), AlarmStatus.MISSED)
            self.books.add(service)
        service.remember_profile(profile)
        return service

    def api_for(self, session: Session) -> JsApi:
        return JsApi(self.service_for(session), self.clock, view=session.view)

    def _login(self, handler: _Exchange) -> None:
        if self.settings.missing_auth():
            _redirect(handler, "/?auth=unconfigured")
            return
        state = secrets.token_urlsafe(24)
        verifier = code_verifier()
        self._pending[state] = (verifier, time.monotonic())
        self._drop_stale_logins()
        _redirect(handler, authorize_url(self.settings, state, verifier))

    def _callback(self, handler: _Exchange, query: dict) -> None:
        state = _one(query, "state")
        code = _one(query, "code")
        pending = self._pending.pop(state, None) if state else None
        if pending is None or time.monotonic() - pending[1] > 600 or not code:
            _redirect(handler, "/?auth=error")
            return
        try:
            profile = exchange_profile(self.settings, code, pending[0])
        except AuthError:
            _redirect(handler, "/?auth=error")
            return
        session_id = secrets.token_urlsafe(32)
        session = Session(
            profile["sub"],
            profile["name"],
            profile["picture"],
        )
        self.sessions[session_id] = session
        self._remember(session_id, session)
        _redirect(handler, "/", cookie=session_id, max_age=SESSION_MAX_AGE)

    def _logout(self, handler: _Exchange) -> None:
        session_id = _cookie_value(handler)
        if session_id:
            self.sessions.pop(session_id, None)
        self._forget()
        _send_json(handler, 200, {"ok": True}, clear_cookie=True)

    def _persist_session(self, session: Session) -> None:
        path = self._session_path()
        if not path.is_file():
            return
        session_id = next((key for key, value in self.sessions.items() if value is session), None)
        if session_id is None:
            return
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            saved = json.loads(open_local(self.master_key, SESSION_AAD, envelope).decode("utf-8"))
        except (OSError, StoreError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
        if saved.get("id") != session_id:
            return
        self._remember(session_id, session)

    def _save_chat(self, session: Session) -> None:
        self._persist_session(session)
        try:
            self.service_for(session).replace_session_log(session.chat_session_id, session.read_chat())
        except StoreError:
            return

    def _remember(self, session_id: str, session: Session) -> None:
        expires = int(self.clock.now().timestamp()) + SESSION_MAX_AGE
        payload = json.dumps(
            {
                "id": session_id,
                "sub": session.sub,
                "name": session.name,
                "picture": session.picture,
                "expires": expires,
                "chat_session_id": session.chat_session_id,
                "chat": session.read_chat(),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        envelope = seal_local(self.master_key, SESSION_AAD, payload)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._session_path()
        temporary = path.with_name("session.json.tmp")
        temporary.write_text(json.dumps(envelope), encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def _load_remembered(self) -> None:
        path = self._session_path()
        if not path.is_file():
            return
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            data = json.loads(open_local(self.master_key, SESSION_AAD, envelope).decode("utf-8"))
            expires = int(data["expires"])
            session_id = data["id"]
            sub = data["sub"]
            name = data["name"]
            if self.clock.now().timestamp() >= expires:
                raise ValueError("expired")
            if not isinstance(session_id, str) or not session_id or not isinstance(sub, str) or not sub:
                raise ValueError("incomplete")
            if not isinstance(name, str) or not name:
                raise ValueError("incomplete")
            picture = data.get("picture")
            session = Session(sub, name, picture if isinstance(picture, str) else None)
            previous = [
                {"role": str(item.get("role") or ""), "text": item["text"]}
                for item in data.get("chat") or []
                if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip()
            ]
            previous_id = data.get("chat_session_id")
            if not isinstance(previous_id, str) or not previous_id:
                previous_id = str(uuid4())
            self.sessions[session_id] = session
            if previous:
                self._archive_previous_chat(sub, name, picture, previous_id, previous)
            self._remember(session_id, session)
        except (OSError, StoreError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            path.unlink(missing_ok=True)

    def _archive_previous_chat(self, sub: str, name: str, picture, chat_session_id: str, messages: list[dict]) -> None:
        profile = auth_profile(sub, name, picture if isinstance(picture, str) else None)
        store = open_user_store(self.root, profile, self.master_key, self.clock, self.settings)
        PlannerService(store, self.clock).replace_session_log(chat_session_id, messages)

    def _forget(self) -> None:
        self._session_path().unlink(missing_ok=True)

    def _session_path(self) -> Path:
        return self.root / "session.json"

    def _chat_activity(self, handler: _Exchange) -> None:
        session = self._session(handler)
        if session is None:
            _send_json(handler, 401, {"ok": False, "missing": self.settings.missing_auth(), "error": "Sign in required"})
            return
        _send_json(handler, 200, {"ok": True, "status": session.read_chat_status()})

    def _session_status(self, handler: _Exchange) -> None:
        session = self._session(handler)
        if session is None:
            _send_json(handler, 401, {"ok": False, "missing": self.settings.missing_auth()})
            return
        _send_json(
            handler,
            200,
            {"ok": True, "display_name": session.name, "avatar_url": session.picture, "chat": session.read_chat()},
        )

    def _rpc(self, session: Session, body: dict) -> tuple[int, dict]:
        name = body.get("name")
        args = body.get("args") if isinstance(body.get("args"), list) else None
        if name not in RPC_ACTIONS or args is None:
            return 400, {"ok": False, "error": "Unknown action"}
        try:
            result = getattr(self.api_for(session), name)(*args)
        except TypeError:
            return 400, {"ok": False, "error": "That action was called with the wrong arguments"}
        if not isinstance(result, dict):
            return 500, {"ok": False, "error": "Action returned an unexpected result"}
        return 200, result

    def _chat(self, session: Session, body: dict) -> tuple[int, dict]:
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            return 400, {"ok": False, "error": "Message is required"}
        text = message.strip()
        session.append_chat("user", text)
        self._save_chat(session)
        if not self.settings.chat_configured:
            error = "Set OLLAMA_MODEL in .env, or GEMINI_API_KEY, or the Azure OpenAI names listed in .env.example."
            session.append_chat("assistant", error)
            self._save_chat(session)
            return 200, {"ok": False, "error": error}
        base, editor, reviewer, identifier = self._chat_models()
        session.set_chat_status("Reading your message")
        try:
            turn = run_turn(
                self.service_for(session),
                text,
                session.view.mode,
                base,
                editor,
                reviewer,
                session.set_chat_status,
                session.chat_session_id,
                identifier,
            )
        except RuntimeError as exc:
            session.append_chat("assistant", str(exc))
            self._save_chat(session)
            return 200, {"ok": False, "error": str(exc)}
        finally:
            session.set_chat_status("")
        session.view.mode = turn.mode
        payload = self.api_for(session).snapshot(turn.task_id)
        reply = turn.reply
        session.append_chat("assistant", reply)
        self._save_chat(session)
        payload["reply"] = reply
        return 200, payload

    def _chat_models(self):
        if self.settings.ollama_model:
            from langchain_ollama import ChatOllama

            shared = {
                "model": self.settings.ollama_model,
                "base_url": self.settings.ollama_base_url,
                "temperature": 0.2,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "num_ctx": OLLAMA_NUM_CTX,
                "client_kwargs": {"timeout": OLLAMA_CHAT_TIMEOUT},
            }

            def ollama(**extra):
                return OneRetry(ChatOllama(**shared, **extra))

            return (
                ollama(format="json"),
                ollama(reasoning=True),
                ollama(format="json"),
                ollama(format="json"),
            )
        if self.settings.gemini_api_key:
            from langchain_google_genai import ChatGoogleGenerativeAI

            def gemini():
                return ChatGoogleGenerativeAI(
                    model=self.settings.gemini_model,
                    google_api_key=self.settings.gemini_api_key,
                    temperature=0.2,
                )

            return gemini(), gemini(), gemini(), gemini()
        from langchain_openai import AzureChatOpenAI

        def azure(deployment: str) -> AzureChatOpenAI:
            return AzureChatOpenAI(
                azure_endpoint=self.settings.azure_endpoint,
                deployment_name=deployment,
                openai_api_version=self.settings.azure_api_version,
                openai_api_key=self.settings.azure_api_key,
                temperature=0.2,
            )

        return (
            azure(self.settings.azure_assistant_deployment),
            azure(self.settings.azure_assistant_deployment),
            azure(self.settings.azure_checker_deployment),
            azure(self.settings.azure_assistant_deployment),
        )

    def _session(self, handler: _Exchange) -> Session | None:
        session_id = _cookie_value(handler)
        if not session_id:
            return None
        return self.sessions.get(session_id)

    def _static(self, handler: _Exchange, name: str) -> None:
        path = self.ui_dir / name
        if not path.is_file():
            _send_json(handler, 404, {"ok": False, "error": "Not found"})
            return
        body = path.read_bytes()
        kind = "text/html; charset=utf-8"
        if name.endswith(".css"):
            kind = "text/css; charset=utf-8"
        elif name.endswith(".js"):
            kind = "text/javascript; charset=utf-8"
        handler.send_response(200)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _drop_stale_logins(self) -> None:
        cutoff = time.monotonic() - 600
        stale = [key for key, value in self._pending.items() if value[1] < cutoff]
        for key in stale:
            self._pending.pop(key, None)

    def _dispatch(self, method: str, exchange: _Exchange) -> None:
        try:
            if method == "GET":
                self.handle_get(exchange)
            elif method == "POST":
                self.handle_post(exchange)
            else:
                _send_json(exchange, 405, {"ok": False, "error": "Not found"})
        except Exception:
            traceback.print_exc()
            if exchange.committed:
                return
            exchange._headers.clear()
            _send_json(exchange, 500, {"ok": False, "error": "Server error"})


def _read_json(handler: _Exchange) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _send_json(handler, status: int, payload: dict, clear_cookie: bool = False) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if clear_cookie:
        handler.send_header("Set-Cookie", "planner_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
    handler.end_headers()
    handler.wfile.write(body)


def _redirect(
    handler: _Exchange,
    location: str,
    cookie: str | None = None,
    max_age: int | None = None,
) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    if cookie:
        handler.send_header("Set-Cookie", _session_cookie(cookie, max_age))
    handler.end_headers()


def _session_cookie(value: str, max_age: int | None = None) -> str:
    parts = [f"planner_session={value}", "HttpOnly", "SameSite=Lax", "Path=/"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


def _cookie_value(handler: _Exchange) -> str:
    for part in handler.headers.get("Cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "planner_session":
            return value
    return ""


def _one(query: dict, key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


async def _lifespan(receive, send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _read_body(receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body") or b"")
        if not message.get("more_body"):
            return b"".join(chunks)
