"""Local HTTP API and the signed-in session. The page is served from ui/."""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from planner.alarms import AlarmBook
from planner.auth import AuthError, authorize_url, code_verifier, exchange_profile
from planner.assistant import AzureChatClient, run_turn
from planner.bridge import JsApi, ViewState
from planner.clock import Clock
from planner.models import AlarmStatus
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
    "set_view_mode",
}

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}


class Session:
    def __init__(self, sub: str, name: str, picture: str | None, maps_key: str) -> None:
        self.sub = sub
        self.name = name
        self.picture = picture
        self.view = ViewState(maps_key)


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


class PlannerApp:
    def __init__(self, settings: Settings, root: Path, clock: Clock, master_key: bytes, ui_dir: Path) -> None:
        self.settings = settings
        self.root = root
        self.clock = clock
        self.master_key = master_key
        self.ui_dir = ui_dir
        self.books = ActiveBooks()
        self.sessions: dict[str, Session] = {}
        self._services: dict[str, PlannerService] = {}
        self._pending: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None

    def serve(self, port: int | None = None) -> ThreadingHTTPServer:
        chosen = self.settings.port if port is None else port
        httpd = ThreadingHTTPServer(("127.0.0.1", chosen), _handler(self))
        thread = threading.Thread(target=httpd.serve_forever, name="planner-http", daemon=True)
        thread.start()
        self._httpd = httpd
        return httpd

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()

    def sign_in_for_tests(self, sub: str, name: str = "Ada") -> str:
        session_id = secrets.token_urlsafe(18)
        self.sessions[session_id] = Session(sub, name, None, self.settings.maps_key)
        return session_id

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
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
        if parsed.path in STATIC_FILES:
            self._static(handler, STATIC_FILES[parsed.path])
            return
        _send_json(handler, 404, {"ok": False, "error": "Not found"})

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
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

    def _login(self, handler: BaseHTTPRequestHandler) -> None:
        if self.settings.missing_auth():
            _redirect(handler, "/?auth=unconfigured")
            return
        state = secrets.token_urlsafe(24)
        verifier = code_verifier()
        self._pending[state] = (verifier, time.monotonic())
        self._drop_stale_logins()
        _redirect(handler, authorize_url(self.settings, state, verifier))

    def _callback(self, handler: BaseHTTPRequestHandler, query: dict) -> None:
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
        self.sessions[session_id] = Session(
            profile["sub"],
            profile["name"],
            profile["picture"],
            self.settings.maps_key,
        )
        _redirect(handler, "/", cookie=session_id)

    def _logout(self, handler: BaseHTTPRequestHandler) -> None:
        session_id = _cookie_value(handler)
        if session_id:
            self.sessions.pop(session_id, None)
        _send_json(handler, 200, {"ok": True}, clear_cookie=True)

    def _session_status(self, handler: BaseHTTPRequestHandler) -> None:
        session = self._session(handler)
        if session is None:
            _send_json(handler, 401, {"ok": False, "missing": self.settings.missing_auth()})
            return
        _send_json(
            handler,
            200,
            {"ok": True, "display_name": session.name, "avatar_url": session.picture},
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
        if not self.settings.chat_configured:
            return 200, {
                "ok": False,
                "error": "Azure OpenAI is not configured. The names are listed in .env.example.",
            }
        assistant = AzureChatClient(
            self.settings.azure_endpoint,
            self.settings.azure_api_key,
            self.settings.azure_assistant_deployment,
            self.settings.azure_api_version,
        )
        checker = AzureChatClient(
            self.settings.azure_endpoint,
            self.settings.azure_api_key,
            self.settings.azure_checker_deployment,
            self.settings.azure_api_version,
        )
        try:
            turn = run_turn(
                self.service_for(session),
                message.strip(),
                session.view.mode,
                assistant,
                checker,
            )
        except RuntimeError as exc:
            return 200, {"ok": False, "error": str(exc)}
        session.view.mode = turn.mode
        payload = self.api_for(session).snapshot(turn.task_id)
        payload["reply"] = turn.reply
        return 200, payload

    def _session(self, handler: BaseHTTPRequestHandler) -> Session | None:
        session_id = _cookie_value(handler)
        if not session_id:
            return None
        return self.sessions.get(session_id)

    def _static(self, handler: BaseHTTPRequestHandler, name: str) -> None:
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


def _handler(app: PlannerApp):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _guard(self, lambda: app.handle_get(self))

        def do_POST(self) -> None:
            _guard(self, lambda: app.handle_post(self))

        def log_message(self, fmt: str, *args) -> None:
            return

    def _guard(handler: BaseHTTPRequestHandler, action) -> None:
        try:
            action()
        except Exception:
            traceback.print_exc()
            try:
                _send_json(handler, 500, {"ok": False, "error": "Server error"})
            except Exception:
                return

    return Handler


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
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


def _redirect(handler: BaseHTTPRequestHandler, location: str, cookie: str | None = None) -> None:
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    if cookie:
        handler.send_header("Set-Cookie", f"planner_session={cookie}; HttpOnly; SameSite=Lax; Path=/")
    handler.end_headers()


def _cookie_value(handler: BaseHTTPRequestHandler) -> str:
    for part in handler.headers.get("Cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "planner_session":
            return value
    return ""


def _one(query: dict, key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""
