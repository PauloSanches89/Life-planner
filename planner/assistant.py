"""Chat clients, and the task edits the LangGraph editor applies."""

from __future__ import annotations

import json
import threading
from datetime import date
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from planner.models import PlannerError, task_to_dict
from planner.service import PlannerService


class ChatClient(Protocol):
    def complete(self, messages: list, tools: list | None = None) -> dict:
        """Return one assistant message."""


class GeminiChatClient:
    """Gemini generateContent. Speaks the same message shape as the Azure client."""

    def __init__(self, api_key: str, model: str, opener=None) -> None:
        self._api_key = api_key
        self._model = model
        self._opener = opener or urlopen

    def complete(self, messages: list, tools: list | None = None) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        request = Request(
            url,
            data=json.dumps(_gemini_body(messages, tools)).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
        )
        try:
            with self._opener(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _gemini_error(exc.read())
            raise RuntimeError(f"Gemini request failed: {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Gemini request failed") from exc
        return _gemini_message(payload)


def _gemini_body(messages: list, tools: list | None) -> dict:
    system = []
    contents = []
    pending: list = []
    names = _tool_names(messages)

    def flush() -> None:
        if pending:
            contents.append({"role": "user", "parts": list(pending)})
            pending.clear()

    for message in messages:
        role = message.get("role")
        if role == "system":
            if message.get("content"):
                system.append({"text": message["content"]})
            continue
        if role == "assistant":
            flush()
            contents.append({"role": "model", "parts": _model_parts(message)})
            continue
        if role == "tool":
            pending.append(_function_response(message, names))
            continue
        if message.get("content"):
            pending.append({"text": message["content"]})
    flush()
    body: dict = {"contents": contents, "generationConfig": {"temperature": 0.2}}
    if system:
        body["systemInstruction"] = {"parts": system}
    if tools:
        body["tools"] = [{"functionDeclarations": [_declaration(tool) for tool in tools]}]
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
    return body


def _tool_names(messages: list) -> dict:
    names = {}
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if call.get("id") and function.get("name"):
                names[call["id"]] = function["name"]
    return names


def _model_parts(message: dict) -> list:
    parts = []
    if message.get("content"):
        parts.append({"text": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        raw = function.get("arguments") or "{}"
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        call_body = {"name": function.get("name") or "", "args": arguments if isinstance(arguments, dict) else {}}
        if call.get("id"):
            call_body["id"] = call["id"]
        parts.append({"functionCall": call_body})
    return parts or [{"text": ""}]


def _function_response(message: dict, names: dict) -> dict:
    raw = message.get("content") or "{}"
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        payload = {"result": raw}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    call_id = message.get("tool_call_id") or ""
    response = {"name": names.get(call_id) or call_id, "response": payload}
    if call_id:
        response["id"] = call_id
    return {"functionResponse": response}


def _declaration(tool: dict) -> dict:
    function = tool.get("function") or {}
    return {
        "name": function.get("name") or "",
        "description": function.get("description") or "",
        "parameters": _gemini_schema(function.get("parameters") or {"type": "object", "properties": {}}),
    }


def _gemini_schema(schema):
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, str):
            cleaned[key] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: _gemini_schema(child) for name, child in value.items()}
        elif key == "items":
            cleaned[key] = _gemini_schema(value)
        else:
            cleaned[key] = value
    return cleaned


def _gemini_message(payload: dict) -> dict:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        feedback = payload.get("promptFeedback") or payload.get("error") or payload
        raise RuntimeError(f"Gemini returned an unexpected payload: {feedback}") from exc
    texts = []
    calls = []
    for index, part in enumerate(parts):
        if part.get("thought"):
            continue
        if part.get("text"):
            texts.append(part["text"])
        call = part.get("functionCall")
        if isinstance(call, dict) and call.get("name"):
            call_id = call.get("id") or f"call-{index}"
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("args") or {}),
                    },
                }
            )
    message = {"role": "assistant", "content": "\n".join(texts)}
    if calls:
        message["tool_calls"] = calls
    return message


def _gemini_error(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:400] or "the request was rejected"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:400]
    return text[:400] or "the request was rejected"


class OllamaChatClient:
    """Local Ollama through its OpenAI-compatible chat endpoint."""

    def __init__(self, base_url: str, model: str, opener=None) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._opener = opener or urlopen

    def complete(self, messages: list, tools: list | None = None) -> dict:
        body: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        else:
            body["response_format"] = {"type": "json_object"}
        request = Request(
            f"{self._base_url}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer ollama"},
        )
        try:
            with self._opener(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Ollama request failed: {_gemini_error(exc.read())}") from exc
        except URLError as exc:
            raise RuntimeError("Ollama is not running. Start Ollama, then try the assistant again.") from exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama request failed") from exc
        return _ollama_message(payload)


OLLAMA_KEEP_ALIVE = "2m"
OLLAMA_REFRESH_SECONDS = 30


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


def _ollama_message(payload: dict) -> dict:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Ollama returned an unexpected payload") from exc
    if not isinstance(message, dict):
        raise RuntimeError("Ollama returned an unexpected payload")
    content = message.get("content")
    calls = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        raw = function.get("arguments") if function.get("arguments") is not None else "{}"
        if not isinstance(raw, str):
            raw = json.dumps(raw)
        calls.append(
            {
                "id": call.get("id") or f"call-{index}",
                "type": "function",
                "function": {"name": function.get("name") or "", "arguments": raw},
            }
        )
    result = {"role": "assistant", "content": content if isinstance(content, str) else ""}
    if calls:
        result["tool_calls"] = calls
    return result


class AzureChatClient:
    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str, opener=None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._deployment = deployment
        self._api_version = api_version
        self._opener = opener or urlopen

    def complete(self, messages: list, tools: list | None = None) -> dict:
        url = (
            f"{self._endpoint}/openai/deployments/{self._deployment}/chat/completions"
            f"?api-version={self._api_version}"
        )
        body: dict = {"messages": messages, "temperature": 0.2}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "api-key": self._api_key},
        )
        try:
            with self._opener(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError("Azure OpenAI request failed") from exc
        try:
            return payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Azure OpenAI returned an unexpected payload") from exc


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


def run_turn(service: PlannerService, user_text: str, mode: str, base, editor, reviewer) -> Turn:
    """Run one user message through the base, editor, and reviewer graph."""
    from planner.graph import run_planner

    return run_planner(service, user_text, mode, base, editor, reviewer)


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


def _apply(service: PlannerService, name: str, arguments: dict) -> tuple[str | None, bool, dict]:
    if name == "list_tasks":
        if arguments.get("day"):
            day = date.fromisoformat(arguments["day"])
            tasks = [task_to_dict(task) for task in service.list_tasks_on(day)]
        else:
            tasks = [task_to_dict(task) for task in service.list_tasks()]
        return None, False, {"ok": True, "tasks": tasks}
    if name == "get_task":
        task = task_to_dict(service.get_task(arguments["task_id"]))
        return arguments["task_id"], False, {"ok": True, "task": task}
    if name == "create_task":
        task = service.create_task(str(arguments.get("title") or "New task"))
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
        for link in arguments.get("links") or []:
            service.add_link(
                task.id,
                str(link["url"]),
                str(link.get("kind") or "generic"),
                str(link.get("label") or ""),
            )
        return task.id, False, {"ok": True, "task_id": task.id}
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
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "delete_task":
        service.delete_task(arguments["task_id"])
        return None, False, {"ok": True}
    if name == "add_subtask":
        service.add_subtask(arguments["task_id"], str(arguments.get("text") or ""))
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "update_subtask":
        service.update_subtask(arguments["task_id"], arguments["subtask_id"], str(arguments.get("text") or ""))
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "delete_subtask":
        service.delete_subtask(arguments["task_id"], arguments["subtask_id"])
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "add_link":
        service.add_link(
            arguments["task_id"],
            str(arguments["url"]),
            str(arguments.get("kind") or "generic"),
            str(arguments.get("label") or ""),
        )
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "delete_link":
        service.delete_link(arguments["task_id"], arguments["link_id"])
        return arguments["task_id"], False, {"ok": True, "task_id": arguments["task_id"]}
    if name == "update_summary":
        service.set_summary(str(arguments.get("summary") or ""))
        return None, True, {"ok": True}
    raise ValueError(f"Unknown tool: {name}")
