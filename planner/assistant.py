"""Azure GPT-4o mini assistant, plus a checker that must allow each edit."""

from __future__ import annotations

import json
from datetime import date
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from planner.models import PlannerError, task_to_dict
from planner.service import PlannerService

MUTATIONS = {
    "create_task",
    "update_task",
    "delete_task",
    "add_subtask",
    "update_subtask",
    "delete_subtask",
    "add_link",
    "delete_link",
    "update_summary",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks that cover a day. Omit day to use the selected day.",
            "parameters": {
                "type": "object",
                "properties": {"day": {"type": "string", "description": "ISO date"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "Read one task, including description, subtasks, links, and location.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a task with the name the user gave. Ask what to add when it is a list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "ongoing": {"type": "boolean"},
                    "subtasks": {"type": "array", "items": {"type": "string"}},
                    "links": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "kind": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["url"],
                        },
                    },
                },
                "required": ["title"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Change fields the user asked to change on an existing task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "ongoing": {"type": "boolean"},
                },
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": "Delete a task the user asked to remove.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_subtask",
            "description": "Add one subtask, such as a shopping-list item the user named.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["task_id", "text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_subtask",
            "description": "Rename a subtask the user asked to change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "subtask_id": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["task_id", "subtask_id", "text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_subtask",
            "description": "Remove a subtask the user asked to remove.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "subtask_id": {"type": "string"}},
                "required": ["task_id", "subtask_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_link",
            "description": "Attach an http or https URL to a task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "url": {"type": "string"},
                    "kind": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["task_id", "url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_link",
            "description": "Remove a URL the user asked to remove.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "link_id": {"type": "string"}},
                "required": ["task_id", "link_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_summary",
            "description": "Save a short standing summary of this user's planner for the next turn.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
]


class ChatClient(Protocol):
    def complete(self, messages: list, tools: list | None = None) -> dict:
        """Return one assistant message."""


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


def run_turn(service: PlannerService, user_text: str, mode: str, assistant: ChatClient, checker: ChatClient | None) -> Turn:
    selected = choose_mode(user_text, mode)
    messages = [
        {"role": "system", "content": _system_prompt(service, selected)},
        {"role": "user", "content": user_text},
    ]
    last_task_id = None
    mutated = False
    summary_updated = False
    reply = ""
    for _round in range(5):
        message = assistant.complete(messages, TOOLS)
        messages.append(_assistant_message(message))
        calls = message.get("tool_calls") or []
        reply = message.get("content") or ""
        if not calls:
            break
        for call in calls:
            name, arguments, task_id, changed, summarized, result = _dispatch(
                service, user_text, checker, call
            )
            if task_id:
                last_task_id = task_id
            mutated = mutated or changed
            summary_updated = summary_updated or summarized
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", name),
                    "content": json.dumps(result),
                }
            )
    if mutated and not summary_updated:
        service.set_summary(_compact_summary(service))
    text = reply.strip() or "Done."
    return Turn(text, selected, last_task_id)


def _system_prompt(service: PlannerService, mode: str) -> str:
    day = service.get_selected_day()
    standing = service.get_summary() or "No standing summary yet."
    tasks = [task_to_dict(task) for task in service.list_tasks_on(day)]
    return (
        "You are the assistant and the manager for this person's planner. "
        "Use tools to read and change only what they asked for. "
        "When they ask you to read tasks, include each description and its subtasks. "
        "When they ask you to create a task, use the name they gave. "
        "If that task is a list, such as shopping, create it and then ask which items to add. "
        "Do not invent list items. "
        f"The current reply mode is {mode}. "
        "In summary mode, keep the reply to titles and one line each. "
        "In detailed mode, include descriptions, subtasks, locations, and links. "
        f"Selected day: {day.isoformat()}. Standing summary: {standing}. "
        f"Tasks on that day: {json.dumps(tasks)}"
    )


def _dispatch(service, user_text, checker, call):
    function = call.get("function") or {}
    name = function.get("name") or ""
    try:
        raw = function.get("arguments") or "{}"
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
    except (ValueError, json.JSONDecodeError) as exc:
        return name, {}, None, False, False, {"ok": False, "error": str(exc)}
    if name in MUTATIONS:
        allowed, reason = _review(checker, user_text, name, arguments, service)
        if not allowed:
            return name, arguments, None, False, False, {"ok": False, "error": reason}
    try:
        task_id, summarized, result = _apply(service, name, arguments)
    except (PlannerError, ValueError, KeyError) as exc:
        return name, arguments, None, False, False, {"ok": False, "error": str(exc)}
    changed = name in MUTATIONS and name != "update_summary"
    return name, arguments, task_id, changed, summarized, result


def _review(checker: ChatClient | None, user_text: str, name: str, arguments: dict, service: PlannerService):
    if checker is None:
        return False, "The checker model is not configured, so the edit was not saved."
    snapshot = _related_task(service, arguments)
    message = checker.complete(
        [
            {
                "role": "system",
                "content": (
                    "You check whether a proposed planner edit is a reasonable reading of the user's request. "
                    'Reply with JSON only: {"allow": true} or {"allow": false, "reason": "why not"}. '
                    "Refuse edits the user did not ask for."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user": user_text,
                        "action": name,
                        "arguments": arguments,
                        "current_task": snapshot,
                    }
                ),
            },
        ]
    )
    return _parse_verdict(message.get("content") or "")


def _parse_verdict(content: str) -> tuple[bool, str]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False, "The checker did not return a decision, so the edit was not saved."
    if not isinstance(payload, dict) or payload.get("allow") is not True:
        reason = payload.get("reason") if isinstance(payload, dict) else None
        return False, reason if isinstance(reason, str) and reason else "The checker refused that edit."
    return True, ""


def _related_task(service: PlannerService, arguments: dict):
    task_id = arguments.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    try:
        return task_to_dict(service.get_task(task_id))
    except PlannerError:
        return None


def _apply(service: PlannerService, name: str, arguments: dict) -> tuple[str | None, bool, dict]:
    if name == "list_tasks":
        day = service.get_selected_day() if not arguments.get("day") else date.fromisoformat(arguments["day"])
        tasks = [task_to_dict(task) for task in service.list_tasks_on(day)]
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
        if arguments.get("start_date"):
            changes["start_date"] = date.fromisoformat(arguments["start_date"])
        if arguments.get("end_date"):
            changes["end_date"] = date.fromisoformat(arguments["end_date"])
        if arguments.get("ongoing") is not None:
            changes["ongoing"] = bool(arguments["ongoing"])
        if changes:
            service.update_task(task.id, **changes)
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


def _compact_summary(service: PlannerService) -> str:
    day = service.get_selected_day()
    lines = []
    for task in service.list_tasks_on(day):
        parts = [task.title]
        if task.location:
            parts.append(task.location)
        if task.description:
            parts.append(task.description)
        lines.append(" — ".join(parts))
    if not lines:
        return f"No tasks on {day.isoformat()}."
    return f"Tasks on {day.isoformat()}: " + "; ".join(lines)


def _assistant_message(message: dict) -> dict:
    kept = {"role": "assistant", "content": message.get("content")}
    if message.get("tool_calls"):
        kept["tool_calls"] = message["tool_calls"]
    return kept
