"""Ollama process helpers, and the task edits the LangGraph editor applies."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

import httpx
from datetime import date
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from planner.models import PlannerError, task_to_dict
from planner.service import PlannerService


OLLAMA_KEEP_ALIVE = "2m"
OLLAMA_REFRESH_SECONDS = 30
OLLAMA_CHAT_TIMEOUT = 90


def ensure_ollama(base_url: str, opener=None, finder=None, popen=None, sleep=None, attempts: int = 40) -> bool:
    """Start the local Ollama server when this app is the one that needs it."""
    if not _is_local_ollama(base_url):
        return False
    opener = opener or urlopen
    if _ollama_is_up(base_url, opener):
        return True
    executable = (finder or find_ollama)()
    if not executable:
        return False
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        (popen or subprocess.Popen)([executable, "serve"], **kwargs)
    except OSError:
        return False
    pause = sleep or time.sleep
    for _ in range(attempts):
        if _ollama_is_up(base_url, opener):
            return True
        pause(0.25)
    return False


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for path in (
        os.path.join(local, "Programs", "Ollama", "ollama.exe"),
        os.path.join(program_files, "Ollama", "ollama.exe"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def _is_local_ollama(base_url: str) -> bool:
    return urlparse(base_url).hostname in {"127.0.0.1", "localhost", "::1"}


def _ollama_is_up(base_url: str, opener) -> bool:
    request = Request(f"{base_url.rstrip('/')}/api/version", method="GET")
    try:
        with opener(request, timeout=1) as response:
            response.read()
    except (URLError, TimeoutError, OSError):
        return False
    return True


class OllamaWarmup:
    """Refresh a two-minute Ollama lease while the app is open, then unload on close."""

    def __init__(self, base_url: str, model: str, opener=None, interval: float = OLLAMA_REFRESH_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._opener = opener or urlopen
        self._interval = interval
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._hold, name="ollama-warmup", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join()
        _ollama_keep_alive(self._base_url, self._model, 0, self._opener, 10)

    def _hold(self) -> None:
        while not self._closed.is_set():
            _ollama_keep_alive(self._base_url, self._model, OLLAMA_KEEP_ALIVE, self._opener, 180)
            self._closed.wait(self._interval)


def _ollama_keep_alive(base_url: str, model: str, keep_alive: int | str, opener, timeout: float) -> None:
    request = Request(
        f"{base_url}/api/generate",
        data=json.dumps({"model": model, "keep_alive": keep_alive}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            response.read()
    except (URLError, TimeoutError, OSError):
        return


class OneRetry:
    """Run one model call again when it times out. A second timeout is returned."""

    def __init__(self, model) -> None:
        self._model = model

    def bind_tools(self, tools, **kwargs):
        return OneRetry(self._model.bind_tools(tools, **kwargs))

    def invoke(self, messages, **kwargs):
        try:
            return self._model.invoke(messages, **kwargs)
        except httpx.TimeoutException:
            return self._model.invoke(messages, **kwargs)


def choose_mode(message: str, current: str) -> str:
    """Detailed when the user asks to read tasks or wants the long form. Summary when they ask for it."""
    text = message.lower()
    if any(phrase in text for phrase in ("in detail", "detailed", "every subtask", "full description")):
        return "detailed"
    if "read my tasks" in text or "read today's tasks" in text:
        return "detailed"
    if any(phrase in text for phrase in ("summary", "summarize", "briefly", "just the titles", "short version")):
        return "summary"
    return current if current in {"summary", "detailed"} else "summary"


class Turn:
    def __init__(self, reply: str, mode: str, task_id: str | None) -> None:
        self.reply = reply
        self.mode = mode
        self.task_id = task_id


def run_turn(
    service: PlannerService,
    user_text: str,
    mode: str,
    base,
    editor,
    reviewer,
    on_status=None,
    chat_session_id: str | None = None,
) -> Turn:
    """Run one user message through the base, editor, and reviewer graph."""
    from planner.graph import run_planner

    return run_planner(service, user_text, mode, base, editor, reviewer, on_status, chat_session_id)


def _span_from_arguments(arguments: dict) -> tuple[date | None, date | None]:
    """One named day fills both ends. A range keeps the first day and the last day."""
    start = _iso_day(arguments.get("start_date"))
    end = _iso_day(arguments.get("end_date"))
    if start is not None and end is None:
        end = start
    elif end is not None and start is None:
        start = end
    return start, end


def _iso_day(value) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return date.fromisoformat(value.strip())


def _apply(service: PlannerService, name: str, arguments: dict) -> tuple[str | None, dict]:
    if name == "list_tasks":
        if arguments.get("day"):
            day = date.fromisoformat(arguments["day"])
            tasks = [task_to_dict(task) for task in service.list_tasks_on(day)]
        else:
            tasks = [task_to_dict(task) for task in service.list_tasks()]
        return None, {"ok": True, "tasks": tasks}
    if name == "get_task":
        task = task_to_dict(service.get_task(arguments["task_id"]))
        return arguments["task_id"], {"ok": True, "task": task}
    if name == "create_task":
        task = service.create_task(
            str(arguments.get("title") or "New task"),
            str(arguments.get("task_id") or "") or None,
        )
        changes = {}
        if arguments.get("description") is not None:
            changes["description"] = str(arguments["description"])
        if arguments.get("location"):
            changes["location"] = str(arguments["location"])
        start_day, end_day = _span_from_arguments(arguments)
        if start_day is not None:
            changes["start_date"] = start_day
            changes["end_date"] = end_day
        if arguments.get("ongoing") is not None:
            changes["ongoing"] = bool(arguments["ongoing"])
        if changes:
            try:
                service.update_task(task.id, **changes)
            except PlannerError:
                service.delete_task(task.id)
                raise
        for text in arguments.get("subtasks") or []:
            service.add_subtask(task.id, str(text))
        return task.id, {"ok": True, "task_id": task.id}
    if name == "update_task":
        changes = {}
        for key in ("title", "description", "location"):
            if arguments.get(key) is not None:
                changes[key] = str(arguments[key])
        if arguments.get("start_date"):
            changes["start_date"] = date.fromisoformat(arguments["start_date"])
        if arguments.get("end_date"):
            changes["end_date"] = date.fromisoformat(arguments["end_date"])
        if arguments.get("ongoing") is not None:
            changes["ongoing"] = bool(arguments["ongoing"])
        service.update_task(arguments["task_id"], **changes)
        return arguments["task_id"], {"ok": True, "task_id": arguments["task_id"]}
    if name == "delete_task":
        service.delete_task(arguments["task_id"])
        return None, {"ok": True}
    raise ValueError(f"Unknown tool: {name}")
