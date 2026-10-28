"""JSON API the desktop window calls. Private helpers are not exposed to the page."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from planner.clock import Clock
from planner.models import Alarm, PlannerError, ResourceLink, Subtask, Task
from planner.service import PlannerService


def _invoke(action):
    try:
        return action()
    except (PlannerError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _link_dict(link: ResourceLink) -> dict:
    return {
        "id": link.id,
        "kind": link.kind.value,
        "url": link.url,
        "label": link.label,
    }


def _iso_date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _task_summary(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "start": _iso_date(task.start_date),
        "end": _iso_date(task.end_date),
        "ongoing": task.ongoing,
        "links": [_link_dict(link) for link in task.links],
    }


def _subtask_dict(subtask: Subtask) -> dict:
    return {"id": subtask.id, "text": subtask.text, "position": subtask.position}


def _alarm_dict(alarm: Alarm) -> dict:
    return {
        "id": alarm.id,
        "fires_at": alarm.fires_at.isoformat(),
        "status": alarm.status.value,
    }


def _task_dict(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "start": _iso_date(task.start_date),
        "end": _iso_date(task.end_date),
        "ongoing": task.ongoing,
        "created_at": task.created_at.isoformat(),
        "subtasks": [_subtask_dict(item) for item in task.subtasks],
        "links": [_link_dict(link) for link in task.links],
        "alarms": [_alarm_dict(alarm) for alarm in task.alarms],
    }


def _label_for(kind: str, label: str | None) -> str:
    text = (label or "").strip()
    if text:
        return text
    if kind == "drawio":
        return "draw.io"
    return "Link"


class JsApi:
    def __init__(
        self,
        service: PlannerService,
        clock: Clock,
        open_url: Callable[[str], object] | None = None,
    ) -> None:
        self._service = service
        self._clock = clock
        self._open_url = webbrowser.open if open_url is None else open_url

    def snapshot(self, task_id=None):
        return _invoke(lambda: self._payload(task_id))

    def set_selected_day(self, iso_date, task_id=None):
        def action():
            self._service.set_selected_day(date.fromisoformat(iso_date))
            return self._payload(task_id)

        return _invoke(action)

    def create_task(self):
        def action():
            task = self._service.create_task()
            return self._payload(task.id)

        return _invoke(action)

    def update_task(self, task_id, title, description, start_date, end_date, ongoing=None):
        def action():
            changes = {}
            if title is not None:
                changes["title"] = title
            if description is not None:
                changes["description"] = description
            if start_date is not None:
                changes["start_date"] = date.fromisoformat(start_date)
            if end_date is not None:
                changes["end_date"] = date.fromisoformat(end_date)
            if ongoing is not None:
                if not isinstance(ongoing, bool):
                    raise ValueError("ongoing must be true or false")
                changes["ongoing"] = ongoing
            self._service.update_task(task_id, **changes)
            return self._payload(task_id)

        return _invoke(action)

    def delete_task(self, task_id):
        def action():
            self._service.delete_task(task_id)
            return self._payload(None)

        return _invoke(action)

    def add_subtask(self, task_id, text=""):
        def action():
            self._service.add_subtask(task_id, "" if text is None else text)
            return self._payload(task_id)

        return _invoke(action)

    def update_subtask(self, task_id, subtask_id, text):
        def action():
            self._service.update_subtask(task_id, subtask_id, text)
            return self._payload(task_id)

        return _invoke(action)

    def delete_subtask(self, task_id, subtask_id):
        def action():
            self._service.delete_subtask(task_id, subtask_id)
            return self._payload(task_id)

        return _invoke(action)

    def add_link(self, task_id, url, kind, label):
        def action():
            self._service.add_link(task_id, url, kind, _label_for(kind, label))
            return self._payload(task_id)

        return _invoke(action)

    def delete_link(self, task_id, link_id):
        def action():
            self._service.delete_link(task_id, link_id)
            return self._payload(task_id)

        return _invoke(action)

    def add_alarm(self, task_id, local_iso):
        def action():
            self._service.add_alarm(task_id, self._aware(local_iso))
            return self._payload(task_id)

        return _invoke(action)

    def delete_alarm(self, task_id, alarm_id):
        def action():
            self._service.delete_alarm(task_id, alarm_id)
            return self._payload(task_id)

        return _invoke(action)

    def undo(self, task_id=None):
        def action():
            self._service.undo()
            return self._payload(task_id)

        return _invoke(action)

    def redo(self, task_id=None):
        def action():
            self._service.redo()
            return self._payload(task_id)

        return _invoke(action)

    def drain_alarm_events(self):
        def action():
            events = self._service.drain_alarm_events()
            return {
                "ok": True,
                "events": [
                    {
                        "task_id": event.task_id,
                        "task_title": event.task_title,
                        "alarm_id": event.alarm_id,
                        "fires_at": event.fires_at.isoformat(),
                        "status": event.status.value,
                    }
                    for event in events
                ],
            }

        return _invoke(action)

    def open_link(self, url):
        def action():
            parsed = urlparse(url or "")
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return {"ok": False, "error": "Only http and https links can be opened"}
            self._open_url(url)
            return {"ok": True}

        return _invoke(action)

    def _aware(self, local_iso: str) -> datetime:
        moment = datetime.fromisoformat(local_iso)
        if moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None:
            return moment
        offset = self._clock.now().utcoffset()
        if offset is None:
            raise ValueError("OS clock has no timezone offset")
        return moment.replace(tzinfo=timezone(offset))

    def _screen(self) -> dict:
        day = self._service.get_selected_day()
        user = self._service.current_user()
        history = self._service.history_state()
        return {
            "today": self._service.today().isoformat(),
            "selected_day": day.isoformat(),
            "tasks": [_task_summary(task) for task in self._service.list_tasks_on(day)],
            "user": {"display_name": user.display_name, "avatar_url": user.avatar_url},
            "can_undo": history.can_undo,
            "can_redo": history.can_redo,
        }

    def _payload(self, task_id: str | None) -> dict:
        screen = self._screen()
        task = None
        if task_id and any(item["id"] == task_id for item in screen["tasks"]):
            task = _task_dict(self._service.get_task(task_id))
        return {"ok": True, "screen": screen, "task": task}
