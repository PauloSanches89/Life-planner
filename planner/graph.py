"""Three LangGraph nodes: collect the change, apply it, then report the real result."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Literal

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from planner.assistant import Turn, _apply, choose_mode
from planner.models import PlannerError, task_to_dict
from planner.service import PlannerService

BASE_PROMPT = (
    "You collect what the user wants changed in their planner. "
    "You do not create, edit, or delete tasks yourself. "
    "Return JSON only, with keys ready, reply, and intake. "
    "intake.action is create, update, delete, restore, or answer. "
    "For create, do not set ready to true until intake has a title, a start_date "
    "(or ongoing true), and alarm set to none or an ISO datetime with a timezone offset. "
    "If the name or the day is missing, ask for both. "
    "If those are present and alarm is missing, ask whether they want an alarm, then wait. "
    "For update, set task_id from the tasks or notes and include only the fields they want changed. "
    "For delete, set task_id of the task they named. "
    "For restore, set task_id of the deleted task they named, using the notes and the deleted list. "
    "For a question or a request to read tasks, set action to answer, ready to false, and put the answer in reply. "
    "Do not invent a title, a date, or an alarm time. "
    "today in the planner JSON is the system date. selected_day is only the day open in the window. "
    "Count today, tonight, tomorrow, and yesterday from today, never from selected_day. "
    "Tomorrow is the day after today. "
    "If the user names a calendar date, use that date. "
    "If they use a relative span such as the next 3 days, and today and selected_day are different, "
    "and they do not say which of those two days to count from, set ready to false and ask. "
    "Name both dates in that question. "
    "When today and selected_day are the same day, count that span from today. "
    "Leave reply empty when ready is true."
)

EDITOR_PROMPT = (
    "Apply the collected intake by calling one tool. "
    "Use only the values in the intake. Do not invent titles, dates, or times. "
    "Do not write a message to the user. "
    "create calls create_task, update calls update_task, delete calls delete_task, and restore calls restore_task. "
    "Copy start_date and end_date from the intake. Do not replace them with selected_day."
)

REVIEWER_PROMPT = (
    "Tell the user what actually happened. Reply with JSON only: {\"reply\": \"...\"}. "
    "Use only the result object. If result.ok is false, say the change did not happen and include result.error. "
    "Do not say it succeeded. "
    "If result.ok is true and the action is create, use the task title and start date, "
    "for example \"Go to Beach successfully created on 1 December 2026\". "
    "If it was updated, deleted, or restored, say that title was successfully updated, deleted, or restored."
)


class PlannerState(TypedDict, total=False):
    user_text: str
    mode: str
    intake: dict
    ready: bool
    outcome: dict
    reply: str
    task_id: str | None


def run_planner(service: PlannerService, user_text: str, mode: str, base, editor, reviewer) -> Turn:
    selected = choose_mode(user_text, mode)
    graph = compile_planner(service, base, editor, reviewer)
    try:
        result = graph.invoke(
            {
                "user_text": user_text,
                "mode": selected,
                "intake": service.get_intake(),
                "ready": False,
                "outcome": {},
                "reply": "",
                "task_id": None,
            }
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(_model_error(exc)) from exc
    outcome = result.get("outcome") or {}
    if result.get("ready") and outcome.get("ok"):
        service.set_intake({})
        _record_note(service, outcome)
    else:
        service.set_intake(result.get("intake") or {})
    reply = (result.get("reply") or "").strip()
    if result.get("ready") and not reply:
        reply = factual_reply(outcome)
    return Turn(reply or "What would you like to change?", selected, result.get("task_id"))


def compile_planner(service: PlannerService, base, editor, reviewer):
    """Each user message starts at the base node. A finished edit returns there on the next message."""
    bucket: list[dict] = []
    tools = _editor_tools(service, bucket)
    by_name = {item.name: item for item in tools}

    def base_node(state: PlannerState) -> dict:
        context = {
            "today": service.today().isoformat(),
            "selected_day": service.get_selected_day().isoformat(),
            "mode": state.get("mode") or "summary",
            "tasks": _compact_tasks(service),
            "notes": service.get_task_notes(),
            "deleted": service.list_deleted(),
            "intake": state.get("intake") or {},
        }
        message = base.invoke(
            [
                SystemMessage(content=BASE_PROMPT + "\nPlanner:\n" + json.dumps(context)),
                HumanMessage(content=state.get("user_text") or ""),
            ]
        )
        return _base_decision(
            message_text(message),
            state.get("user_text") or "",
            state.get("intake") or {},
            service,
        )

    def editor_node(state: PlannerState) -> dict:
        bucket.clear()
        bound = editor.bind_tools(tools)
        messages = [
            SystemMessage(content=EDITOR_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "today": service.today().isoformat(),
                        "selected_day": service.get_selected_day().isoformat(),
                        "intake": state.get("intake") or {},
                        "tasks": _compact_tasks(service),
                        "deleted": service.list_deleted(),
                    }
                )
            ),
        ]
        action = (state.get("intake") or {}).get("action") or "edit"
        for _round in range(5):
            message = bound.invoke(messages)
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                break
            messages.append(message)
            intake = state.get("intake") or {}
            for call in calls:
                args = _dates_from_intake(call.get("args") or {}, intake)
                payload = _run_call(by_name, bucket, {**call, "args": args}, action)
                messages.append(ToolMessage(content=payload, tool_call_id=call.get("id") or "call"))
            if bucket:
                break
        outcome = _outcome(bucket, action)
        task = outcome.get("task") or {}
        return {"outcome": outcome, "task_id": task.get("id")}

    def reviewer_node(state: PlannerState) -> dict:
        outcome = state.get("outcome") or {}
        message = reviewer.invoke(
            [
                SystemMessage(content=REVIEWER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "user": state.get("user_text") or "",
                            "request": state.get("intake") or {},
                            "result": outcome,
                        }
                    )
                ),
            ]
        )
        parsed = _parse_json(message_text(message)) or {}
        reply = parsed.get("reply") if isinstance(parsed.get("reply"), str) else ""
        return {"reply": honest_reply(reply, outcome)}

    graph = StateGraph(PlannerState)
    graph.add_node("base", base_node)
    graph.add_node("editor", editor_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_edge(START, "base")
    graph.add_conditional_edges("base", _after_base, ["editor", END])
    graph.add_edge("editor", "reviewer")
    graph.add_edge("reviewer", END)
    return graph.compile()


def _after_base(state: PlannerState) -> Literal["editor", "__end__"]:
    if state.get("ready"):
        return "editor"
    return END


def _base_decision(content: str, user_text: str, previous: dict, service: PlannerService) -> dict:
    payload = _parse_json(content) or {}
    incoming = _clean_intake(payload.get("intake"))
    intake = _merge_intake(previous, incoming)
    question = _apply_date_policy(user_text, intake, service.today(), service.get_selected_day())
    if question:
        return {"intake": intake, "ready": False, "reply": question}
    action = str(intake.get("action") or "")
    gap = _missing(intake, {item["id"] for item in service.list_deleted()})
    reply = payload.get("reply") if isinstance(payload.get("reply"), str) else ""
    reply = reply.strip()
    if action == "answer":
        return {"intake": {}, "ready": False, "reply": reply or "What would you like to know?"}
    ready = payload.get("ready") is True and gap is None and action in {"create", "update", "delete", "restore"}
    if ready:
        return {"intake": intake, "ready": True, "reply": ""}
    if payload.get("ready") is True or not reply:
        reply = gap or "What would you like to change?"
    return {"intake": intake, "ready": False, "reply": reply}


def _apply_date_policy(user_text: str, intake: dict, today: date, selected: date) -> str | None:
    """Pin today and tomorrow to the system date. Ask when a span could start on either day."""
    text = user_text.lower()
    if intake.get("date_anchor") == "unresolved":
        if _pins_selected(text) and not _pins_system(text):
            intake["date_anchor"] = "selected"
            _fill_anchor_start(intake, selected, today)
            return None
        if _pins_system(text) or _single_day(text) == "today":
            intake["date_anchor"] = "system"
            _fill_anchor_start(intake, today, selected)
            return None
        return _anchor_question(today, selected)
    if re.search(r"\d{4}-\d{2}-\d{2}", text) and not _is_span(text):
        intake["date_anchor"] = "explicit"
        return None
    span = _is_span(text)
    single = _single_day(text)
    pin_system = _pins_system(text) or single is not None
    pin_selected = _pins_selected(text)
    if span and pin_system and pin_selected:
        intake["date_anchor"] = "unresolved"
        return _anchor_question(today, selected)
    if span and pin_system:
        intake["date_anchor"] = "system"
        _fill_anchor_start(intake, today, selected)
        return None
    if span and pin_selected:
        intake["date_anchor"] = "selected"
        _fill_anchor_start(intake, selected, today)
        return None
    if span and selected != today:
        intake["date_anchor"] = "unresolved"
        return _anchor_question(today, selected)
    if span:
        intake["date_anchor"] = "system"
        _fill_anchor_start(intake, today, selected)
        return None
    if single:
        day = today + timedelta(days={"tomorrow": 1, "yesterday": -1}.get(single, 0))
        intake["start_date"] = day.isoformat()
        intake["end_date"] = day.isoformat()
        intake["ongoing"] = False
        intake["date_anchor"] = "system"
    return None


def _fill_anchor_start(intake: dict, anchor_day: date, replace: date | None = None) -> None:
    current = str(intake.get("start_date") or "")
    if not current or (replace is not None and current == replace.isoformat()):
        intake["start_date"] = anchor_day.isoformat()


def _anchor_question(today: date, selected: date) -> str:
    return (
        f"Do you mean from today ({_spoken_day(today.isoformat())}), "
        f"or from the day open in the planner ({_spoken_day(selected.isoformat())})?"
    )


def _is_span(text: str) -> bool:
    return bool(re.search(r"\b(next|upcoming|following|coming)\b.{0,40}\bday", text))


def _single_day(text: str) -> str | None:
    if re.search(r"\btonight\b", text):
        return "today"
    for word in ("tomorrow", "yesterday", "today"):
        if re.search(rf"\b{word}\b", text):
            return word
    return None


def _pins_system(text: str) -> bool:
    return bool(re.search(r"\b(from today|from now|current date|system date)\b", text))


def _pins_selected(text: str) -> bool:
    return bool(
        re.search(
            r"\b(selected day|selected date|open day|day open|day i'm viewing|day i am viewing|day i'm editing|day i am editing)\b",
            text,
        )
    )


def _dates_from_intake(args: dict, intake: dict) -> dict:
    if intake.get("date_anchor") not in ("system", "selected", "explicit"):
        return args
    start = intake.get("start_date")
    if not isinstance(start, str) or not start:
        return args
    copied = dict(args)
    copied["start_date"] = start
    end = intake.get("end_date")
    if isinstance(end, str) and end:
        copied["end_date"] = end
    return copied


def _missing(intake: dict, deleted_ids: set[str]) -> str | None:
    action = str(intake.get("action") or "")
    if action == "create":
        titled = bool(str(intake.get("title") or "").strip())
        dated = bool(str(intake.get("start_date") or "").strip()) or intake.get("ongoing") is True
        if not titled and not dated:
            return "What should the task be called, and which day is it on?"
        if not titled:
            return "What should the task be called?"
        if not dated:
            return "Which day should the task be on?"
        if not _alarm_decided(intake.get("alarm")):
            return "Would you like to set an alarm?"
        return None
    if action == "update":
        if not str(intake.get("task_id") or "").strip():
            return "Which task should I change?"
        if not _has_change(intake):
            return "What should I change on that task?"
        return None
    if action == "delete":
        if not str(intake.get("task_id") or "").strip():
            return "Which task should I delete?"
        return None
    if action == "restore":
        task_id = str(intake.get("task_id") or "").strip()
        if task_id not in deleted_ids:
            return "Which deleted task should I put back?"
        return None
    return "What would you like to change?"


def _has_change(intake: dict) -> bool:
    for key in ("title", "description", "location", "start_date", "end_date", "alarm"):
        value = str(intake.get(key) or "").strip()
        if key == "alarm" and value == "none":
            continue
        if value:
            return True
    if intake.get("ongoing") is True:
        return True
    subtasks = intake.get("subtasks")
    return isinstance(subtasks, list) and any(str(item).strip() for item in subtasks)


def _alarm_decided(value) -> bool:
    if value == "none":
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return False
    return moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None


def _clean_intake(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key in (
        "action",
        "task_id",
        "title",
        "start_date",
        "end_date",
        "description",
        "location",
        "alarm",
        "date_anchor",
    ):
        value = raw.get(key)
        if isinstance(value, str):
            cleaned[key] = value.strip()
    if isinstance(raw.get("ongoing"), bool):
        cleaned["ongoing"] = raw["ongoing"]
    subtasks = raw.get("subtasks")
    if isinstance(subtasks, list):
        cleaned["subtasks"] = [str(item).strip() for item in subtasks if str(item).strip()]
    return cleaned


def _merge_intake(previous: dict, new: dict) -> dict:
    merged = dict(previous or {})
    for key, value in new.items():
        if value == "" or value == []:
            continue
        merged[key] = value
    return merged


def _editor_tools(service: PlannerService, bucket: list[dict]):
    def record(result: dict) -> str:
        bucket.append(result)
        return json.dumps(result)

    @tool
    def list_tasks(day: str = "") -> str:
        """List tasks. Pass an ISO date to limit to that day, or leave day empty for every task."""
        arguments = {"day": day} if day else {}
        _task_id, _summarized, result = _apply(service, "list_tasks", arguments)
        return json.dumps(result)

    @tool
    def get_task(task_id: str) -> str:
        """Read one task by id."""
        _task_id, _summarized, result = _apply(service, "get_task", {"task_id": task_id})
        return json.dumps(result)

    @tool
    def create_task(
        title: str,
        start_date: str = "",
        end_date: str = "",
        ongoing: bool = False,
        description: str = "",
        location: str = "",
        alarm: str = "",
        subtasks: list[str] | None = None,
    ) -> str:
        """Create the task from the collected intake. Do not invent fields."""
        arguments: dict = {"title": title, "ongoing": ongoing}
        if start_date:
            arguments["start_date"] = start_date
        if end_date:
            arguments["end_date"] = end_date
        if description:
            arguments["description"] = description
        if location:
            arguments["location"] = location
        if subtasks:
            arguments["subtasks"] = subtasks
        try:
            task_id, _summarized, result = _apply(service, "create_task", arguments)
        except PlannerError as exc:
            return record({"ok": False, "action": "create", "error": str(exc), "task": None})
        if not result.get("ok") or not task_id:
            return record({"ok": False, "action": "create", "error": "The task was not saved.", "task": None})
        saved = task_to_dict(service.get_task(task_id))
        if alarm and alarm != "none":
            try:
                service.add_alarm(task_id, datetime.fromisoformat(alarm))
                saved = task_to_dict(service.get_task(task_id))
            except (PlannerError, ValueError) as exc:
                return record(
                    {
                        "ok": False,
                        "action": "create",
                        "error": f"The task was saved, but the alarm was not set: {exc}",
                        "task": saved,
                    }
                )
        return record({"ok": True, "action": "create", "error": "", "task": saved})

    @tool
    def update_task(
        task_id: str,
        title: str = "",
        description: str = "",
        location: str = "",
        start_date: str = "",
        end_date: str = "",
        ongoing: bool = False,
        alarm: str = "",
    ) -> str:
        """Change an existing task using only the collected fields."""
        arguments: dict = {"task_id": task_id}
        if title:
            arguments["title"] = title
        if description:
            arguments["description"] = description
        if location:
            arguments["location"] = location
        if start_date:
            arguments["start_date"] = start_date
        if end_date:
            arguments["end_date"] = end_date
        if ongoing:
            arguments["ongoing"] = True
        try:
            _apply(service, "update_task", arguments)
            saved = task_to_dict(service.get_task(task_id))
        except PlannerError as exc:
            return record({"ok": False, "action": "update", "error": str(exc), "task": None})
        if alarm and alarm != "none":
            try:
                service.add_alarm(task_id, datetime.fromisoformat(alarm))
                saved = task_to_dict(service.get_task(task_id))
            except (PlannerError, ValueError) as exc:
                return record(
                    {
                        "ok": False,
                        "action": "update",
                        "error": f"The task was saved, but the alarm was not set: {exc}",
                        "task": saved,
                    }
                )
        return record({"ok": True, "action": "update", "error": "", "task": saved})

    @tool
    def delete_task(task_id: str) -> str:
        """Delete the task with this id and keep a snapshot so it can be restored."""
        try:
            snapshot = task_to_dict(service.get_task(task_id))
            _apply(service, "delete_task", {"task_id": task_id})
            service.remember_deleted(snapshot)
        except PlannerError as exc:
            return record({"ok": False, "action": "delete", "error": str(exc), "task": None})
        return record({"ok": True, "action": "delete", "error": "", "task": snapshot})

    @tool
    def restore_task(task_id: str) -> str:
        """Put back a deleted task by the id stored with its note."""
        try:
            task = service.restore_deleted(task_id)
        except PlannerError as exc:
            return record({"ok": False, "action": "restore", "error": str(exc), "task": None})
        return record({"ok": True, "action": "restore", "error": "", "task": task_to_dict(task)})

    return [list_tasks, get_task, create_task, update_task, delete_task, restore_task]


def _run_call(by_name: dict, bucket: list[dict], call: dict, action: str) -> str:
    name = call.get("name") or ""
    args = call.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    chosen = by_name.get(name)
    if chosen is None:
        result = {"ok": False, "action": action, "error": f"Unknown tool: {name}", "task": None}
        bucket.append(result)
        return json.dumps(result)
    try:
        payload = chosen.invoke(args)
    except Exception as exc:
        result = {"ok": False, "action": action, "error": str(exc), "task": None}
        bucket.append(result)
        return json.dumps(result)
    if isinstance(payload, str):
        return payload
    return json.dumps(payload)


def _outcome(bucket: list[dict], action: str) -> dict:
    if not bucket:
        return {"ok": False, "action": action, "error": "The editor did not change a task.", "task": None}
    failed = next((item for item in reversed(bucket) if not item.get("ok")), None)
    if failed:
        return {
            "ok": False,
            "action": failed.get("action") or action,
            "error": failed.get("error") or "The edit failed.",
            "task": failed.get("task"),
        }
    last = bucket[-1]
    return {"ok": True, "action": last.get("action") or action, "error": "", "task": last.get("task")}


def _compact_tasks(service: PlannerService) -> list[dict]:
    items = []
    for task in service.list_tasks():
        items.append(
            {
                "id": task.id,
                "title": task.title,
                "start_date": task.start_date.isoformat() if task.start_date else None,
                "end_date": task.end_date.isoformat() if task.end_date else None,
                "ongoing": task.ongoing,
            }
        )
    return items


def _record_note(service: PlannerService, outcome: dict) -> None:
    task = outcome.get("task") or {}
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        return
    line = _note_line(outcome)
    previous = service.get_task_notes().get(task_id, "")
    summary = f"{previous} {line}".strip() if previous else line
    service.set_task_note(task_id, summary)
    service.set_summary(summary)


def _note_line(outcome: dict) -> str:
    task = outcome.get("task") or {}
    title = task.get("title") or "Task"
    when = task.get("start_date") or "no date"
    action = outcome.get("action")
    if action == "create":
        return f"{title}: created on {when}."
    if action == "delete":
        return f"{title}: deleted. Was on {when}."
    if action == "restore":
        return f"{title}: restored."
    return f"{title}: updated."


def factual_reply(outcome: dict) -> str:
    task = outcome.get("task") or {}
    title = task.get("title") or "The task"
    action = outcome.get("action") or "edit"
    if not outcome.get("ok"):
        reason = outcome.get("error") or "The edit did not happen."
        verb = {"create": "create", "update": "update", "delete": "delete", "restore": "restore"}.get(action, "edit")
        return f"Could not {verb} the task. {reason}"
    if action == "create":
        if task.get("ongoing"):
            return f"{title} successfully created, with no end date"
        spoken = _spoken_day(task.get("start_date"))
        if spoken:
            return f"{title} successfully created on {spoken}"
        return f"{title} successfully created"
    if action == "delete":
        return f"{title} successfully deleted"
    if action == "restore":
        return f"{title} successfully restored"
    return f"{title} successfully updated"


def honest_reply(reply: str, outcome: dict) -> str:
    text = reply.strip()
    fact = factual_reply(outcome)
    if not text:
        return fact
    claims_success = "success" in text.lower()
    if outcome.get("ok"):
        return text if claims_success else fact
    if claims_success:
        return fact
    return text


def _spoken_day(value) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        day = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{day.day} {day.strftime('%B %Y')}"


def message_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return str(content or "")


def _parse_json(content: str) -> dict | None:
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    return payload


def _model_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "connection" in text or "refused" in text or "11434" in text:
        return "Ollama is not running. Start Ollama, then try the assistant again."
    return str(exc) or "The assistant request failed"
