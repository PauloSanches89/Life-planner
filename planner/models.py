"""Task document and the errors the domain raises."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class PlannerError(Exception):
    """Base error for domain operations."""


class NotFoundError(PlannerError):
    """A task, subtask, link, or alarm id is missing."""


class InvalidSpan(PlannerError):
    """A task end date is before its start date."""


class EmptyHistory(PlannerError):
    """Undo or redo was requested with nothing on that stack."""


class InvalidAlarm(PlannerError):
    """An alarm time is missing a timezone or the outcome is not allowed."""


class InvalidLink(PlannerError):
    """A resource link is missing a url or uses an unknown kind."""


class StoreError(PlannerError):
    """The on-disk document is missing or has an unsupported shape."""


class LinkKind(str, Enum):
    DRAWIO = "drawio"
    GENERIC = "generic"


class AlarmStatus(str, Enum):
    SCHEDULED = "scheduled"
    FIRED = "fired"
    MISSED = "missed"


@dataclass
class Subtask:
    id: str
    text: str
    position: int


@dataclass
class ResourceLink:
    id: str
    kind: LinkKind
    url: str
    label: str


@dataclass
class Alarm:
    id: str
    fires_at: datetime
    status: AlarmStatus


@dataclass(frozen=True)
class AlarmEvent:
    task_id: str
    task_title: str
    alarm_id: str
    fires_at: datetime
    status: AlarmStatus


@dataclass
class Task:
    id: str
    title: str
    description: str
    start_date: date
    end_date: date
    created_at: datetime
    subtasks: list[Subtask]
    links: list[ResourceLink]
    alarms: list[Alarm]


@dataclass
class Profile:
    id: str
    display_name: str
    avatar_url: str | None
    provider: str


@dataclass
class Document:
    version: int
    profile: Profile
    selected_day: date
    tasks: list[Task]


STORE_VERSION = 1


def covers(task: Task, day: date) -> bool:
    """True when day sits inside the task span, including both ends."""
    return task.start_date <= day <= task.end_date


def parse_link_kind(value: str) -> LinkKind:
    try:
        return LinkKind(value)
    except ValueError as exc:
        raise InvalidLink(f"Unknown link kind: {value}") from exc


def require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise InvalidAlarm("Alarm times must include a timezone offset from the OS clock")
    return moment


def subtask_to_dict(subtask: Subtask) -> dict:
    return {"id": subtask.id, "text": subtask.text, "position": subtask.position}


def link_to_dict(link: ResourceLink) -> dict:
    return {
        "id": link.id,
        "kind": link.kind.value,
        "url": link.url,
        "label": link.label,
    }


def alarm_to_dict(alarm: Alarm) -> dict:
    return {
        "id": alarm.id,
        "fires_at": alarm.fires_at.isoformat(),
        "status": alarm.status.value,
    }


def task_to_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "start_date": task.start_date.isoformat(),
        "end_date": task.end_date.isoformat(),
        "created_at": task.created_at.isoformat(),
        "subtasks": [subtask_to_dict(item) for item in task.subtasks],
        "links": [link_to_dict(item) for item in task.links],
        "alarms": [alarm_to_dict(item) for item in task.alarms],
    }


def document_to_dict(document: Document) -> dict:
    return {
        "version": document.version,
        "profile": {
            "id": document.profile.id,
            "display_name": document.profile.display_name,
            "avatar_url": document.profile.avatar_url,
            "provider": document.profile.provider,
        },
        "selected_day": document.selected_day.isoformat(),
        "tasks": [task_to_dict(task) for task in document.tasks],
    }


def _require_mapping(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise StoreError(f"{label} must be an object")
    return value


def _require_str(data: dict, key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise StoreError(f"{label}.{key} must be a string")
    return value


def _require_int(data: dict, key: str, label: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError(f"{label}.{key} must be an integer")
    return value


def _require_date(data: dict, key: str, label: str) -> date:
    raw = _require_str(data, key, label)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise StoreError(f"{label}.{key} must be an ISO date") from exc


def _require_datetime(data: dict, key: str, label: str) -> datetime:
    raw = _require_str(data, key, label)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise StoreError(f"{label}.{key} must be an ISO datetime") from exc


def subtask_from_dict(data: object) -> Subtask:
    raw = _require_mapping(data, "subtask")
    return Subtask(
        id=_require_str(raw, "id", "subtask"),
        text=_require_str(raw, "text", "subtask"),
        position=_require_int(raw, "position", "subtask"),
    )


def link_from_dict(data: object) -> ResourceLink:
    raw = _require_mapping(data, "link")
    kind_raw = _require_str(raw, "kind", "link")
    try:
        kind = LinkKind(kind_raw)
    except ValueError as exc:
        raise StoreError(f"Unknown link kind: {kind_raw}") from exc
    return ResourceLink(
        id=_require_str(raw, "id", "link"),
        kind=kind,
        url=_require_str(raw, "url", "link"),
        label=_require_str(raw, "label", "link"),
    )


def alarm_from_dict(data: object) -> Alarm:
    raw = _require_mapping(data, "alarm")
    status_raw = _require_str(raw, "status", "alarm")
    try:
        status = AlarmStatus(status_raw)
    except ValueError as exc:
        raise StoreError(f"Unknown alarm status: {status_raw}") from exc
    try:
        fires_at = require_aware(_require_datetime(raw, "fires_at", "alarm"))
    except InvalidAlarm as exc:
        raise StoreError("alarm.fires_at must include a timezone offset") from exc
    return Alarm(id=_require_str(raw, "id", "alarm"), fires_at=fires_at, status=status)


def task_from_dict(data: object) -> Task:
    raw = _require_mapping(data, "task")
    subtasks_raw = raw.get("subtasks", [])
    links_raw = raw.get("links", [])
    alarms_raw = raw.get("alarms", [])
    if not isinstance(subtasks_raw, list) or not isinstance(links_raw, list) or not isinstance(alarms_raw, list):
        raise StoreError("task collections must be lists")
    subtasks = [subtask_from_dict(item) for item in subtasks_raw]
    subtasks.sort(key=lambda item: item.position)
    return Task(
        id=_require_str(raw, "id", "task"),
        title=_require_str(raw, "title", "task"),
        description=_require_str(raw, "description", "task"),
        start_date=_require_date(raw, "start_date", "task"),
        end_date=_require_date(raw, "end_date", "task"),
        created_at=_require_datetime(raw, "created_at", "task"),
        subtasks=subtasks,
        links=[link_from_dict(item) for item in links_raw],
        alarms=[alarm_from_dict(item) for item in alarms_raw],
    )


def document_from_dict(data: object) -> Document:
    raw = _require_mapping(data, "document")
    if raw.get("version") != STORE_VERSION:
        raise StoreError(f"Unsupported store version: {raw.get('version')}")
    profile_raw = _require_mapping(raw.get("profile"), "profile")
    avatar = profile_raw.get("avatar_url")
    if avatar is not None and not isinstance(avatar, str):
        raise StoreError("profile.avatar_url must be a string or null")
    tasks_raw = raw.get("tasks", [])
    if not isinstance(tasks_raw, list):
        raise StoreError("tasks must be a list")
    return Document(
        version=STORE_VERSION,
        profile=Profile(
            id=_require_str(profile_raw, "id", "profile"),
            display_name=_require_str(profile_raw, "display_name", "profile"),
            avatar_url=avatar,
            provider=_require_str(profile_raw, "provider", "profile"),
        ),
        selected_day=_require_date(raw, "selected_day", "document"),
        tasks=[task_from_dict(item) for item in tasks_raw],
    )
