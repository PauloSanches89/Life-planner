"""Three LangGraph nodes: collect the change, apply it, then report the real result."""

from __future__ import annotations

import inspect
import json
import re
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import uuid4

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from planner.assistant import Turn, _apply, choose_mode
from planner.models import PlannerError, task_to_dict
from planner.service import PlannerService

BASE_PROMPT = (
    "You read the user and fill intake. You do not create, edit, or delete tasks yourself. "
    "Return JSON only, with keys ready, reply, and intake. "
    "On this message, decide whether they want a new task or a change to the chosen task. "
    "A new task uses action create and an empty task_id. The app assigns the id. Do not invent an id. "
    "An edit uses action update and the id in task_state. "
    "Delete and restore use those actions and the id in task_state. "
    "When task_state is empty and this is not a new task, ask which task. "
    "Leave start_date and end_date empty unless the user states a duration, such as 5 November to 10 November. "
    "An empty duration stays ongoing. "
    "Set alarm to yes or no. If they name a time, put an ISO datetime with a timezone offset in alarm. "
    "If they want an alarm but name no time, set alarm to yes and ask once what time to use. "
    "Do not ask a third time. After two questions, set ready to true with the fields you have. "
    "now is the current time. today is the system date. selected_day is only the day open in the window. "
    "Count today, tonight, tomorrow, and yesterday from today, never from selected_day. "
    "Tomorrow is the day after today. A named calendar date is that date. "
    "If a relative span such as the next 3 days could start on either today or selected_day, "
    "set ready to false and ask in your own words, including both dates. "
    "When those dates are the same day, count the span from today. "
    "task_sessions are summaries of earlier chats about this task, one for each past session. "
    "They are not part of the current chat. "
    "task_state is the saved task when this is an edit. "
    "Fill intake to this schema and repair typos so the values are usable. "
    "action is create, update, delete, restore, or answer. "
    "title, description, and location are strings. A place named with for, at, or from goes in location. "
    "subtasks is an array of strings, one item each, even when the user omits commas or misspells a word. "
    "start_date and end_date are YYYY-MM-DD or empty. "
    "Creating a task needs a title, and whether the task is persistent or locked to a date. "
    "Persistent means no start or end date, so ongoing is true. Locked to a date means start_date and end_date are filled. "
    "Description, location, subtasks, an alarm, and links can be added, and they are not required. "
    "When the user asks what information is needed to create a task, set action to answer and ready to false, "
    "and explain those requirements in your own words. Do not ask them to choose an existing task. "
    "A list is action create. Its title is the list name, such as shopping list. s hopping means shopping. "
    "When ready is false, write your own short polite question in reply about what is still missing. "
    "Leave reply empty when ready is true. "
    "reviewer_reply is the question the user was just asked. offer is the detail that question is about. "
    "asked is the request that question came from. "
    "When offer is set and the user agrees, set action to update and fill that detail from their answer and from asked. "
    "When the offer is alarm and they agree, set the alarm. "
    "If they name a time, or asked names a time, put an ISO datetime with a timezone offset in alarm. "
    "If they agree and no time is known, set alarm to yes and ask what time. "
    "When they decline, set action to answer and acknowledge in reply. "
    "When they change the subject, ignore reviewer_reply, offer, and asked. "
    "choices are the only tasks this message may change. "
    "Set action to update and task_id to one of those ids. Do not create a task while choices are set. "
    "If they still do not say which one, set ready to false and ask which one."
)

EDITOR_PROMPT = (
    "You call tools. Think in one short sentence, name the one tool the action requires, then stop thinking and call it. "
    "Do not compare options. Do not write a message to the user. Do not invent fields. "
    "action create calls create_task. action update calls update_task. "
    "Pass the title, description, duration, location, and subtasks from state. "
    "Call set_alarm in a separate call only when alarm is a datetime. "
    "Call add_link in a separate call for each link. "
    "action delete calls only delete_task. action restore calls only restore_task."
)

IDENTIFIER_PROMPT = (
    "You choose the one existing task the user means. Reply with JSON only: {\"task_id\": \"\"}. "
    "tasks and deleted contain id and title only. "
    "Set task_id to the one id the user means. "
    "Leave task_id empty when none of the titles fit. "
    "Do not invent an id."
)

REVIEWER_PROMPT = (
    "You write the reply the user will read. Reply with JSON only: {\"reply\": \"...\", \"offer\": \"\"}. "
    "Be helpful and polite. Say what was saved in one short sentence. "
    "Do not describe, correct, or explain an alarm. Do not say what the app cannot do. "
    "If the user asked for something that was not saved, ask one short question offering to add it "
    "and set offer to that detail. "
    "For a missing alarm, ask \"Would you like me to set an alarm?\" and set offer to alarm. "
    "When nothing they asked for is missing, leave offer empty. "
    "Do not claim a step that is not in the steps list."
)


class PlannerState(TypedDict, total=False):
    user_text: str
    mode: str
    intake: dict
    ready: bool
    outcome: dict
    reply: str
    offer: str
    task_id: str | None


def run_planner(
    service: PlannerService,
    user_text: str,
    mode: str,
    base,
    editor,
    reviewer,
    on_status=None,
    chat_session_id: str | None = None,
    identifier=None,
) -> Turn:
    selected = choose_mode(user_text, mode)
    graph = compile_planner(service, base, editor, reviewer, on_status, chat_session_id, identifier)
    try:
        result = graph.invoke(
            {
                "user_text": user_text,
                "mode": selected,
                "intake": service.get_intake(),
                "ready": False,
                "outcome": {},
                "reply": "",
                "offer": "",
                "task_id": None,
            }
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(_model_error(exc)) from exc
    outcome = result.get("outcome") or {}
    reply = (result.get("reply") or "").strip()
    if result.get("ready") and outcome.get("ok"):
        offer = str(result.get("offer") or "").strip()
        saved_id = ((outcome.get("task") or {}).get("id")) or result.get("task_id")
        if offer and saved_id:
            service.set_intake(
                {
                    "task_id": saved_id,
                    "reviewer_reply": reply,
                    "offer": offer,
                    "asked": user_text,
                }
            )
        else:
            service.set_intake({})
        _record_note(service, outcome)
    else:
        service.set_intake(result.get("intake") or {})
    _remember_turn(service, user_text, reply, result, chat_session_id)
    return Turn(reply, selected, result.get("task_id"))


def compile_planner(
    service: PlannerService,
    base,
    editor,
    reviewer,
    on_status=None,
    chat_session_id: str | None = None,
    identifier=None,
):
    """Each user message starts at the base node. A finished edit returns there on the next message."""
    bucket: list[dict] = []
    tools = _editor_tools(service, bucket)
    by_name = {item.name: item for item in tools}

    def base_node(state: PlannerState) -> dict:
        _report_status(on_status, "Reading your message")
        intake = state.get("intake") or {}
        user_text = state.get("user_text") or ""
        shown = str(intake.get("task_id") or "").strip()
        if intake.get("choices") and not _wants_new_task(user_text):
            return _ask_base(state, shown, intake)
        if not shown and intake.get("action") != "create" and not _wants_new_task(user_text):
            hits = _title_hits(user_text, service.list_tasks())
            if len(hits) > 1:
                return {
                    "intake": {"choices": [task.id for task in hits], "asked": user_text},
                    "ready": False,
                    "reply": _clarify_matches(user_text, hits),
                }
            if len(hits) == 1:
                shown = hits[0].id
            elif identifier is not None:
                _report_status(on_status, "Finding the task")
                shown = _identify_task(identifier, user_text, service)
                _report_status(on_status, "Reading your message")
        return _ask_base(state, shown, intake)

    def _ask_base(state: PlannerState, task_key: str, previous: dict) -> dict:
        context = {
            "now": service.now().isoformat(),
            "today": service.today().isoformat(),
            "selected_day": service.get_selected_day().isoformat(),
            "mode": state.get("mode") or "summary",
            "intake": previous,
            "reviewer_reply": previous.get("reviewer_reply") or "",
            "offer": previous.get("offer") or "",
            "asked": previous.get("asked") or "",
            "choices": _choice_rows(service, previous.get("choices") or []),
            "task_state": _chosen_task(service, task_key),
            "task_sessions": service.earlier_task_sessions(task_key, chat_session_id) if task_key else [],
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
            previous,
            service,
        )

    def editor_node(state: PlannerState) -> dict:
        _report_status(on_status, "Applying the change")
        bucket.clear()
        bound = editor.bind_tools(tools)
        messages = [
            SystemMessage(content=EDITOR_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "now": service.now().isoformat(),
                        "today": service.today().isoformat(),
                        "selected_day": service.get_selected_day().isoformat(),
                        "state": _editor_state(service, state.get("intake") or {}),
                    }
                )
            ),
        ]
        action = (state.get("intake") or {}).get("action") or "edit"
        planned = _editor_state(service, state.get("intake") or {})
        intake = state.get("intake") or {}
        required = _required_call(action, planned)
        if required:
            _run_call(by_name, bucket, required, action, on_status)
        for _round in range(5):
            if required or (
                bucket
                and (not _missing_steps(intake, bucket) or any(not item.get("ok") for item in bucket))
            ):
                break
            message = bound.invoke(messages)
            calls = getattr(message, "tool_calls", None) or []
            if not calls:
                break
            messages.append(message)
            for call in calls:
                args = _args_from_state(call.get("args") or {}, planned, call.get("name") or "")
                payload = _run_call(by_name, bucket, {**call, "args": args}, action, on_status)
                messages.append(ToolMessage(content=payload, tool_call_id=call.get("id") or "call"))
        outcome = _outcome(bucket, action)
        missing = _missing_steps(state.get("intake") or {}, bucket)
        if missing:
            note = "The editor did not " + " or ".join(missing) + "."
            outcome = {
                "ok": False,
                "action": outcome.get("action") or action,
                "error": note if not outcome.get("error") or outcome.get("ok") else f"{outcome.get('error')} {note}",
                "task": outcome.get("task"),
            }
        task = outcome.get("task") or {}
        return {"outcome": outcome, "task_id": task.get("id")}

    def reviewer_node(state: PlannerState) -> dict:
        _report_status(on_status, "Writing a reply")
        outcome = state.get("outcome") or {}
        message = reviewer.invoke(
            [
                SystemMessage(content=REVIEWER_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "user": state.get("user_text") or "",
                            "state": _editor_state(service, state.get("intake") or {}),
                            "steps": [
                                {"tool": item.get("tool"), "ok": item.get("ok"), "error": item.get("error")}
                                for item in bucket
                            ],
                            "result": outcome,
                        }
                    )
                ),
            ]
        )
        parsed = _parse_json(message_text(message)) or {}
        reply = parsed.get("reply") if isinstance(parsed.get("reply"), str) else ""
        offer = parsed.get("offer") if isinstance(parsed.get("offer"), str) else ""
        return {"reply": reply.strip(), "offer": offer.strip()}

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
    choices = [item for item in (previous.get("choices") or []) if isinstance(item, str) and item]
    asked = previous.get("asked") or user_text
    payload = _parse_json(content) or {}
    incoming = _clean_intake(payload.get("intake"))
    previous = _blank_new_task(previous, incoming, service)
    intake = _merge_intake(previous, incoming)
    intake = _assign_task_id(intake, previous, service, user_text)
    reply = payload.get("reply") if isinstance(payload.get("reply"), str) else ""
    reply = reply.strip()
    action = str(intake.get("action") or "")
    if choices and not _wants_new_task(user_text):
        chosen = str(intake.get("task_id") or "")
        if action != "update" or chosen not in set(choices):
            return {
                "intake": {"choices": choices, "asked": asked},
                "ready": False,
                "reply": reply or "Which one should I change?",
            }
    if _apply_date_policy(user_text, intake, service.today(), service.get_selected_day()):
        if _asks(intake) >= 2:
            _default_duration(intake)
            intake["date_anchor"] = "system"
        else:
            intake["asks"] = _asks(intake) + 1
            return {"intake": intake, "ready": False, "reply": reply}
    _normalize_alarm(intake)
    _default_duration(intake)
    if action == "answer":
        return {"intake": {}, "ready": False, "reply": reply}
    ask = _next_question(intake, service)
    if ask:
        if ask == "alarm":
            intake["alarm_asked"] = True
        intake["asks"] = _asks(intake) + 1
        return {"intake": intake, "ready": False, "reply": reply}
    if intake.get("alarm") == "yes":
        intake["alarm"] = "no"
        intake["alarm_skipped"] = True
    if action == "create" and not str(intake.get("title") or "").strip():
        intake["title"] = "New task"
    ready = (
        not _intake_incomplete(intake, {item["id"] for item in service.list_deleted()})
        and action in {"create", "update", "delete", "restore"}
    )
    if ready:
        return {"intake": intake, "ready": True, "reply": ""}
    return {"intake": intake, "ready": False, "reply": reply}


def _blank_new_task(previous: dict, incoming: dict, service: PlannerService) -> dict:
    action = str(incoming.get("action") or previous.get("action") or "")
    if action != "create":
        return previous
    previous_id = str(previous.get("task_id") or "").strip()
    live = {task.id for task in service.list_tasks()}
    deleted = {item["id"] for item in service.list_deleted()}
    continuing = previous.get("action") == "create" and previous_id not in live and previous_id not in deleted
    if continuing:
        return previous
    return {}


def _asks(intake: dict) -> int:
    value = intake.get("asks") or 0
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _next_question(intake: dict, service: PlannerService) -> str | None:
    """Name the missing field. The base model writes the question the user reads."""
    if _asks(intake) >= 2:
        return None
    action = str(intake.get("action") or "")
    if action == "create" and not str(intake.get("title") or "").strip():
        return "title"
    if action == "update" and not str(intake.get("task_id") or "").strip():
        return "task"
    if action == "delete" and not str(intake.get("task_id") or "").strip():
        return "task"
    if action == "restore" and str(intake.get("task_id") or "").strip() not in {
        item["id"] for item in service.list_deleted()
    }:
        return "task"
    if action == "update" and not _has_change(intake):
        return "change"
    if intake.get("alarm") == "yes" and intake.get("alarm_asked") is not True:
        return "alarm"
    return None


def _normalize_alarm(intake: dict) -> None:
    raw = str(intake.get("alarm") or "").strip()
    lowered = raw.lower()
    if lowered in {"", "no", "none", "false"}:
        intake["alarm"] = "no"
        return
    if lowered in {"yes", "true", "y"}:
        intake["alarm"] = "yes"
        return
    if _alarm_decided(raw):
        intake["alarm"] = raw
        return
    intake["alarm"] = "no"


def _default_duration(intake: dict) -> None:
    start = str(intake.get("start_date") or "").strip()
    end = str(intake.get("end_date") or "").strip()
    if start or end:
        intake["ongoing"] = False
        if start and not end:
            intake["end_date"] = start
        if end and not start:
            intake["start_date"] = end
        return
    if intake.get("action") == "create":
        intake["ongoing"] = True


def _assign_task_id(intake: dict, previous: dict, service: PlannerService, user_text: str) -> dict:
    """Keep an existing task id, or assign one when this is a new task."""
    action = str(intake.get("action") or "")
    proposed = str(intake.get("task_id") or "").strip()
    previous_id = str((previous or {}).get("task_id") or "").strip()
    live = {task.id for task in service.list_tasks()}
    deleted = {item["id"] for item in service.list_deleted()}
    if action == "create":
        if previous_id and previous_id not in live and previous_id not in deleted:
            intake["task_id"] = previous_id
        elif proposed and proposed not in live and proposed not in deleted:
            intake["task_id"] = proposed
        else:
            intake["task_id"] = str(uuid4())
        return intake
    if action in {"update", "delete"}:
        if proposed in live:
            return intake
        if previous.get("offer") and previous_id in live:
            intake["task_id"] = previous_id
            return intake
        matched = _match_task(user_text, service.list_tasks()) or _match_title(str(intake.get("title") or ""), service.list_tasks())
        if matched:
            intake["task_id"] = matched
        else:
            intake.pop("task_id", None)
        return intake
    if action == "restore":
        if proposed not in deleted:
            intake.pop("task_id", None)
    return intake


def _wants_new_task(user_text: str) -> bool:
    text = user_text.lower()
    if any(phrase in text for phrase in ("new task", "make a task", "make an ongoing", "add a task")):
        return True
    return re.search(r"\bcreate\b", text) is not None


def _clarify_matches(user_text: str, hits: list) -> str:
    """Ask which task, before any edit, when one title matches more than one task."""
    title = hits[0].title.strip()
    same = all(task.title.strip().lower() == title.lower() for task in hits)
    ongoing = [task for task in hits if task.ongoing]
    if same and len(ongoing) > 1:
        lead = f"You have {len(ongoing)} ongoing tasks named {title}."
    elif same:
        lead = f"You have {len(hits)} tasks named {title}."
    else:
        lead = "More than one task matches."
    change = "add the alarm to" if "alarm" in user_text.lower() else "change"
    if same and len(ongoing) == len(hits):
        return f"{lead} Which one should I {change}?"
    details = []
    for task in hits:
        if task.ongoing:
            details.append(f"{task.title}, ongoing")
        elif task.start_date:
            details.append(f"{task.title}, {task.start_date.isoformat()}")
        else:
            details.append(task.title)
    return f"{lead} Which one should I {change}? " + "; ".join(details) + "."


def _choice_rows(service: PlannerService, ids: list) -> list[dict]:
    rows = []
    for task_id in ids:
        if not isinstance(task_id, str):
            continue
        saved = _saved_task(service, task_id)
        if not saved:
            continue
        rows.append(
            {
                "id": saved["id"],
                "title": saved["title"],
                "ongoing": saved.get("ongoing") is True,
                "start_date": saved.get("start_date"),
            }
        )
    return rows


def _title_hits(user_text: str, tasks: list) -> list:
    text = user_text.lower()
    hits = []
    for task in tasks:
        title = task.title.lower().strip()
        if len(title) >= 3 and title in text:
            hits.append(task)
    return hits


def _match_task(user_text: str, tasks: list) -> str:
    hits = _title_hits(user_text, tasks)
    if len(hits) == 1:
        return hits[0].id
    return ""


def _identify_task(identifier, user_text: str, service: PlannerService) -> str:
    live = [{"id": task.id, "title": task.title} for task in service.list_tasks()]
    deleted = [{"id": item["id"], "title": item["title"]} for item in service.list_deleted()]
    allowed = {item["id"] for item in live + deleted}
    message = identifier.invoke(
        [
            SystemMessage(content=IDENTIFIER_PROMPT),
            HumanMessage(content=json.dumps({"message": user_text, "tasks": live, "deleted": deleted})),
        ]
    )
    payload = _parse_json(message_text(message)) or {}
    task_id = str(payload.get("task_id") or "").strip()
    if task_id in allowed:
        return task_id
    return ""


def _chosen_task(service: PlannerService, task_id: str) -> dict | None:
    if not task_id:
        return None
    note = service.get_task_notes().get(task_id, "")
    saved = _saved_task(service, task_id)
    if saved:
        if note:
            saved["note"] = note
        return saved
    for item in service.list_deleted():
        if item["id"] != task_id:
            continue
        return {
            "id": item["id"],
            "title": item["title"],
            "start_date": item.get("start_date"),
            "end_date": item.get("end_date"),
            "note": item.get("note") or note,
        }
    return None


def _match_title(title: str, tasks: list) -> str:
    cleaned = title.lower().strip()
    if not cleaned:
        return ""
    hits = [task for task in tasks if task.title.lower().strip() == cleaned]
    if len(hits) == 1:
        return hits[0].id
    return ""


def _saved_task(service: PlannerService, task_id: str) -> dict | None:
    if not task_id:
        return None
    try:
        task = service.get_task(task_id)
    except PlannerError:
        return None
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "location": task.location,
        "start_date": task.start_date.isoformat() if task.start_date else None,
        "end_date": task.end_date.isoformat() if task.end_date else None,
        "ongoing": task.ongoing,
    }


def _editor_state(service: PlannerService, intake: dict) -> dict:
    state = {
        "now": service.now().isoformat(),
        "action": intake.get("action") or "",
        "task_id": intake.get("task_id") or "",
        "title": intake.get("title") or "",
        "description": intake.get("description") or "",
        "location": intake.get("location") or "",
        "start_date": intake.get("start_date") or "",
        "end_date": intake.get("end_date") or "",
        "ongoing": intake.get("ongoing") is True,
        "alarm": intake.get("alarm") or "no",
        "links": intake.get("links") or [],
        "subtasks": intake.get("subtasks") if isinstance(intake.get("subtasks"), list) else [],
    }
    if state["action"] != "update" or not state["task_id"]:
        return state
    saved = _saved_task(service, state["task_id"])
    if not saved:
        return state
    if not state["title"]:
        state["title"] = saved["title"]
    if not state["description"]:
        state["description"] = saved["description"]
    if not state["start_date"] and not state["ongoing"]:
        if saved["ongoing"]:
            state["ongoing"] = True
        elif saved["start_date"]:
            state["start_date"] = saved["start_date"]
            state["end_date"] = saved["end_date"] or saved["start_date"]
    return state


def _required_call(action: str, state: dict) -> dict | None:
    """Delete and restore use the id already chosen. A create call must not run instead."""
    task_id = str(state.get("task_id") or "").strip()
    if not task_id:
        return None
    if action == "delete":
        return {"name": "delete_task", "args": {"task_id": task_id}, "id": "delete-task", "type": "tool_call"}
    if action == "restore":
        return {"name": "restore_task", "args": {"task_id": task_id}, "id": "restore-task", "type": "tool_call"}
    return None


def _args_from_state(args: dict, state: dict, name: str) -> dict:
    copied = dict(args or {})
    task_id = str(state.get("task_id") or "").strip()
    if task_id and name in {
        "create_task",
        "update_task",
        "delete_task",
        "restore_task",
        "set_alarm",
        "add_link",
    }:
        copied["task_id"] = task_id
    if name in {"create_task", "update_task"}:
        if state.get("title"):
            copied["title"] = state["title"]
        if state.get("description"):
            copied["description"] = state["description"]
        if str(state.get("location") or "").strip():
            copied["location"] = state["location"]
        if state.get("ongoing") is True and not state.get("start_date"):
            copied["ongoing"] = True
        elif state.get("start_date"):
            copied["start_date"] = state["start_date"]
            copied["end_date"] = state.get("end_date") or state["start_date"]
            copied["ongoing"] = False
        items = [str(item).strip() for item in (state.get("subtasks") or []) if str(item).strip()]
        if name == "create_task" and items:
            copied["subtasks"] = items
    if name == "set_alarm" and _alarm_time(state.get("alarm")):
        copied["when"] = state["alarm"]
    links = state.get("links") or []
    if name == "add_link" and len(links) == 1 and not copied.get("url"):
        copied["url"] = links[0].get("url") or ""
        copied["label"] = links[0].get("label") or ""
    return copied


def _alarm_time(value) -> bool:
    return _alarm_decided(value) and str(value).strip().lower() not in {"none", "no"}


def _missing_steps(intake: dict, bucket: list[dict]) -> list[str]:
    called = {item.get("tool") for item in bucket if item.get("ok")}
    missing = []
    action = str(intake.get("action") or "")
    needed = {
        "create": ("create_task", "create the task"),
        "update": ("update_task", "update the task"),
        "delete": ("delete_task", "delete the task"),
        "restore": ("restore_task", "restore the task"),
    }
    if action in needed and needed[action][0] not in called:
        missing.append(needed[action][1])
    if _alarm_time(intake.get("alarm")) and "set_alarm" not in called:
        missing.append("set the alarm")
    if intake.get("links") and "add_link" not in called:
        missing.append("add the link")
    return missing


def _apply_date_policy(user_text: str, intake: dict, today: date, selected: date) -> bool:
    """Pin today and tomorrow to the system date. A true result means the base model should ask."""
    text = user_text.lower()
    if intake.get("date_anchor") == "unresolved":
        if _pins_selected(text) and not _pins_system(text):
            intake["date_anchor"] = "selected"
            _fill_anchor_start(intake, selected, today)
            return False
        if _pins_system(text) or _single_day(text) == "today":
            intake["date_anchor"] = "system"
            _fill_anchor_start(intake, today, selected)
            return False
        return True
    if re.search(r"\d{4}-\d{2}-\d{2}", text) and not _is_span(text):
        intake["date_anchor"] = "explicit"
        return False
    span = _is_span(text)
    single = _single_day(text)
    pin_system = _pins_system(text) or single is not None
    pin_selected = _pins_selected(text)
    if span and pin_system and pin_selected:
        intake["date_anchor"] = "unresolved"
        return True
    if span and pin_system:
        intake["date_anchor"] = "system"
        _fill_anchor_start(intake, today, selected)
        return False
    if span and pin_selected:
        intake["date_anchor"] = "selected"
        _fill_anchor_start(intake, selected, today)
        return False
    if span and selected != today:
        intake["date_anchor"] = "unresolved"
        return True
    if span:
        intake["date_anchor"] = "system"
        _fill_anchor_start(intake, today, selected)
        return False
    if single:
        day = today + timedelta(days={"tomorrow": 1, "yesterday": -1}.get(single, 0))
        intake["start_date"] = day.isoformat()
        intake["end_date"] = day.isoformat()
        intake["ongoing"] = False
        intake["date_anchor"] = "system"
    return False


def _fill_anchor_start(intake: dict, anchor_day: date, replace: date | None = None) -> None:
    current = str(intake.get("start_date") or "")
    if not current or (replace is not None and current == replace.isoformat()):
        intake["start_date"] = anchor_day.isoformat()


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


def _intake_incomplete(intake: dict, deleted_ids: set[str]) -> bool:
    action = str(intake.get("action") or "")
    if action == "create":
        return not str(intake.get("title") or "").strip()
    if action == "update":
        return not str(intake.get("task_id") or "").strip() or not _has_change(intake)
    if action == "delete":
        return not str(intake.get("task_id") or "").strip()
    if action == "restore":
        return str(intake.get("task_id") or "").strip() not in deleted_ids
    return True


def _has_change(intake: dict) -> bool:
    for key in ("title", "description", "location", "start_date", "end_date", "alarm"):
        value = str(intake.get(key) or "").strip()
        if key == "alarm" and value in {"none", "no"}:
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
    links = raw.get("links")
    if isinstance(links, list):
        cleaned["links"] = [item for item in links if isinstance(item, dict) and str(item.get("url") or "").strip()]
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
        _task_id, result = _apply(service, "list_tasks", arguments)
        return json.dumps(result)

    @tool
    def get_task(task_id: str) -> str:
        """Read one task by id."""
        _task_id, result = _apply(service, "get_task", {"task_id": task_id})
        return json.dumps(result)

    @tool
    def create_task(
        title: str,
        task_id: str = "",
        start_date: str = "",
        end_date: str = "",
        ongoing: bool = False,
        description: str = "",
        location: str = "",
        subtasks: list[str] | dict | None = None,
    ) -> str:
        """Create the task. Subtasks are optional and are omitted when none were given."""
        arguments: dict = {"title": title, "ongoing": ongoing}
        if task_id:
            arguments["task_id"] = task_id
        if start_date:
            arguments["start_date"] = start_date
        if end_date:
            arguments["end_date"] = end_date
        if description:
            arguments["description"] = description
        if location:
            arguments["location"] = location
        if isinstance(subtasks, list):
            items = [str(item).strip() for item in subtasks if str(item).strip()]
            if items:
                arguments["subtasks"] = items
        try:
            task_id, result = _apply(service, "create_task", arguments)
        except PlannerError as exc:
            return record({"ok": False, "tool": "create_task", "action": "create", "error": str(exc), "task": None})
        if not result.get("ok") or not task_id:
            return record({"ok": False, "tool": "create_task", "action": "create", "error": "The task was not saved.", "task": None})
        saved = task_to_dict(service.get_task(task_id))
        return record({"ok": True, "tool": "create_task", "action": "create", "error": "", "task": saved})

    @tool
    def update_task(
        task_id: str,
        title: str = "",
        description: str = "",
        location: str = "",
        start_date: str = "",
        end_date: str = "",
        ongoing: bool = False,
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
            return record({"ok": False, "tool": "update_task", "action": "update", "error": str(exc), "task": None})
        return record({"ok": True, "tool": "update_task", "action": "update", "error": "", "task": saved})

    @tool
    def set_alarm(task_id: str, when: str) -> str:
        """Set an alarm on the task. when is an ISO datetime with a timezone offset."""
        try:
            service.add_alarm(task_id, datetime.fromisoformat(when))
            saved = task_to_dict(service.get_task(task_id))
        except (PlannerError, ValueError) as exc:
            return record({"ok": False, "tool": "set_alarm", "action": "update", "error": str(exc), "task": None})
        return record({"ok": True, "tool": "set_alarm", "action": "update", "error": "", "task": saved})

    @tool
    def add_link(task_id: str, url: str, label: str = "", kind: str = "generic") -> str:
        """Add one link to the task."""
        try:
            service.add_link(task_id, url, kind or "generic", label)
            saved = task_to_dict(service.get_task(task_id))
        except PlannerError as exc:
            return record({"ok": False, "tool": "add_link", "action": "update", "error": str(exc), "task": None})
        return record({"ok": True, "tool": "add_link", "action": "update", "error": "", "task": saved})

    @tool
    def delete_task(task_id: str) -> str:
        """Delete the task with this id and keep a snapshot so it can be restored."""
        try:
            snapshot = task_to_dict(service.get_task(task_id))
            _apply(service, "delete_task", {"task_id": task_id})
            service.remember_deleted(snapshot)
        except PlannerError as exc:
            return record({"ok": False, "tool": "delete_task", "action": "delete", "error": str(exc), "task": None})
        return record({"ok": True, "tool": "delete_task", "action": "delete", "error": "", "task": snapshot})

    @tool
    def restore_task(task_id: str) -> str:
        """Put back a deleted task by the id stored with its note."""
        try:
            task = service.restore_deleted(task_id)
        except PlannerError as exc:
            return record({"ok": False, "tool": "restore_task", "action": "restore", "error": str(exc), "task": None})
        return record({"ok": True, "tool": "restore_task", "action": "restore", "error": "", "task": task_to_dict(task)})

    return [
        list_tasks,
        get_task,
        create_task,
        update_task,
        set_alarm,
        add_link,
        delete_task,
        restore_task,
    ]


_STEP_STATUS = {
    "list_tasks": "Retrieving task data",
    "get_task": "Retrieving task data",
    "create_task": "Creating task",
    "update_task": "Updating task",
    "set_alarm": "Setting an alarm",
    "add_link": "Adding a link",
    "delete_task": "Deleting task",
    "restore_task": "Restoring task",
}


def _report_status(on_status, text: str) -> None:
    if on_status is not None:
        on_status(text)


def _run_call(by_name: dict, bucket: list[dict], call: dict, action: str, on_status=None) -> str:
    name = call.get("name") or ""
    _report_status(on_status, _STEP_STATUS.get(name, "Working on your request"))
    args = call.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    chosen = by_name.get(name)
    if chosen is None:
        result = {"ok": False, "action": action, "error": f"Unknown tool: {name}", "task": None}
        bucket.append(result)
        return json.dumps(result)
    try:
        payload = _invoke_tool(chosen, args)
    except Exception as exc:
        result = {"ok": False, "action": action, "error": str(exc), "task": None}
        bucket.append(result)
        return json.dumps(result)
    if isinstance(payload, str):
        return payload
    return json.dumps(payload)


def _invoke_tool(chosen, args: dict):
    func = getattr(chosen, "func", None)
    if func is not None:
        allowed = set(inspect.signature(func).parameters)
        args = {key: value for key, value in args.items() if key in allowed}
    subtasks = args.get("subtasks")
    if "subtasks" in args and not isinstance(subtasks, list):
        args = {key: value for key, value in args.items() if key != "subtasks"}
    return chosen.invoke(args)


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


def _remember_turn(
    service: PlannerService,
    user_text: str,
    reply: str,
    result: dict,
    chat_session_id: str | None = None,
) -> None:
    intake = result.get("intake") or {}
    outcome = result.get("outcome") or {}
    task = outcome.get("task") or {}
    task_id = task.get("id") if isinstance(task.get("id"), str) else ""
    if not task_id:
        task_id = str(intake.get("task_id") or "").strip()
    if not task_id:
        return
    service.append_transcript(
        task_id,
        [{"role": "user", "text": user_text}, {"role": "assistant", "text": reply}],
    )
    if chat_session_id:
        service.record_task_session(task_id, chat_session_id, user_text, reply)


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
    if "timed out" in text or "timeout" in text:
        return "The assistant took too long. Try again."
    if "connection" in text or "refused" in text or "11434" in text:
        return "Ollama is not running. Start Ollama, then try the assistant again."
    return str(exc) or "The assistant request failed"
