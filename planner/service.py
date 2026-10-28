"""Operations the UI will call. Every user edit persists immediately."""

from __future__ import annotations

import copy
import threading
from datetime import date, datetime
from uuid import uuid4

from planner.clock import Clock
from planner.history import History, HistoryState
from planner.identity import AnonymousIdentity, Identity
from planner.models import (
    Alarm,
    AlarmEvent,
    AlarmStatus,
    Document,
    InvalidAlarm,
    InvalidLink,
    InvalidSpan,
    NotFoundError,
    Profile,
    ResourceLink,
    Subtask,
    Task,
    covers,
    parse_link_kind,
    require_aware,
)
from planner.store import Store


class PlannerService:
    def __init__(
        self,
        store: Store,
        clock: Clock,
        identity: Identity | None = None,
        history_limit: int = 100,
    ) -> None:
        self._store = store
        self._clock = clock
        self._identity: Identity = identity if identity is not None else AnonymousIdentity(store)
        self._history = History(limit=history_limit)
        self._events: list[AlarmEvent] = []
        self._lock = threading.Lock()

    def current_user(self) -> Profile:
        with self._lock:
            return self._identity.current_user()

    def today(self) -> date:
        return require_aware(self._clock.now()).date()

    def get_selected_day(self) -> date:
        with self._lock:
            return self._store.load().selected_day

    def set_selected_day(self, day: date) -> None:
        with self._lock:
            document = self._store.load()
            if document.selected_day == day:
                return
            document.selected_day = day
            self._store.save(document)

    def list_tasks_on(self, day: date) -> list[Task]:
        with self._lock:
            document = self._store.load()
            return copy.deepcopy([task for task in document.tasks if covers(task, day)])

    def get_task(self, task_id: str) -> Task:
        with self._lock:
            return copy.deepcopy(self._find(self._store.load(), task_id))

    def create_task(self) -> Task:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = Task(
                id=str(uuid4()),
                title="New task",
                description="",
                start_date=document.selected_day,
                end_date=document.selected_day,
                created_at=require_aware(self._clock.now()),
                subtasks=[],
                links=[],
                alarms=[],
                ongoing=False,
            )
            document.tasks.append(task)
            self._persist_tasks(document, before)
            return copy.deepcopy(task)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        ongoing: bool | None = None,
    ) -> Task:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            new_ongoing = task.ongoing if ongoing is None else ongoing
            if new_ongoing:
                new_start = None
                new_end = None
            else:
                new_start = task.start_date if start_date is None else start_date
                new_end = task.end_date if end_date is None else end_date
                if new_start is None or new_end is None:
                    raise InvalidSpan("A task needs a start and end date unless it is ongoing")
                if new_end < new_start:
                    raise InvalidSpan("Task end date must be on or after the start date")
            if title is not None:
                task.title = title
            if description is not None:
                task.description = description
            task.start_date = new_start
            task.end_date = new_end
            task.ongoing = new_ongoing
            self._persist_tasks(document, before)
            return copy.deepcopy(task)

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            if not any(task.id == task_id for task in document.tasks):
                raise NotFoundError(f"Task not found: {task_id}")
            document.tasks = [task for task in document.tasks if task.id != task_id]
            self._persist_tasks(document, before)

    def add_subtask(self, task_id: str, text: str = "") -> Subtask:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            subtask = Subtask(id=str(uuid4()), text=text, position=len(task.subtasks))
            task.subtasks.append(subtask)
            self._persist_tasks(document, before)
            return copy.deepcopy(subtask)

    def update_subtask(self, task_id: str, subtask_id: str, text: str) -> Subtask:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            subtask = self._find_subtask(document, task_id, subtask_id)
            subtask.text = text
            self._persist_tasks(document, before)
            return copy.deepcopy(subtask)

    def delete_subtask(self, task_id: str, subtask_id: str) -> None:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            kept = [item for item in task.subtasks if item.id != subtask_id]
            if len(kept) == len(task.subtasks):
                raise NotFoundError(f"Subtask not found: {subtask_id}")
            for index, item in enumerate(kept):
                item.position = index
            task.subtasks = kept
            self._persist_tasks(document, before)

    def add_link(self, task_id: str, url: str, kind: str, label: str) -> ResourceLink:
        if not url.strip():
            raise InvalidLink("Link url is required")
        parsed_kind = parse_link_kind(kind)
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            link = ResourceLink(id=str(uuid4()), kind=parsed_kind, url=url, label=label)
            task.links.append(link)
            self._persist_tasks(document, before)
            return copy.deepcopy(link)

    def delete_link(self, task_id: str, link_id: str) -> None:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            kept = [item for item in task.links if item.id != link_id]
            if len(kept) == len(task.links):
                raise NotFoundError(f"Link not found: {link_id}")
            task.links = kept
            self._persist_tasks(document, before)

    def add_alarm(self, task_id: str, fires_at: datetime) -> Alarm:
        require_aware(fires_at)
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            alarm = Alarm(id=str(uuid4()), fires_at=fires_at, status=AlarmStatus.SCHEDULED)
            task.alarms.append(alarm)
            self._persist_tasks(document, before)
            return copy.deepcopy(alarm)

    def delete_alarm(self, task_id: str, alarm_id: str) -> None:
        with self._lock:
            document = self._store.load()
            before = copy.deepcopy(document.tasks)
            task = self._find(document, task_id)
            kept = [item for item in task.alarms if item.id != alarm_id]
            if len(kept) == len(task.alarms):
                raise NotFoundError(f"Alarm not found: {alarm_id}")
            task.alarms = kept
            self._persist_tasks(document, before)

    def undo(self) -> None:
        with self._lock:
            document = self._store.load()
            restored = self._history.preview_undo()
            current = document.tasks
            self._keep_alarm_status(restored, current)
            document.tasks = restored
            self._store.save(document)
            self._history.commit_undo(current)

    def redo(self) -> None:
        with self._lock:
            document = self._store.load()
            restored = self._history.preview_redo()
            current = document.tasks
            self._keep_alarm_status(restored, current)
            document.tasks = restored
            self._store.save(document)
            self._history.commit_redo(current)

    def history_state(self) -> HistoryState:
        with self._lock:
            return self._history.state()

    def drain_alarm_events(self) -> list[AlarmEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def transition_due_alarms(self, now: datetime, outcome: AlarmStatus) -> None:
        require_aware(now)
        if outcome not in (AlarmStatus.FIRED, AlarmStatus.MISSED):
            raise InvalidAlarm("Alarm transitions must be fired or missed")
        with self._lock:
            document = self._store.load()
            events: list[AlarmEvent] = []
            for task in document.tasks:
                for alarm in task.alarms:
                    if alarm.status != AlarmStatus.SCHEDULED:
                        continue
                    if alarm.fires_at <= now:
                        alarm.status = outcome
                        events.append(
                            AlarmEvent(
                                task_id=task.id,
                                task_title=task.title,
                                alarm_id=alarm.id,
                                fires_at=alarm.fires_at,
                                status=outcome,
                            )
                        )
            if not events:
                return
            self._store.save(document)
            self._events.extend(events)

    def _keep_alarm_status(self, restored: list[Task], current: list[Task]) -> None:
        """Fired and missed are system state, so undo restores text without rewinding them."""
        statuses = {alarm.id: alarm.status for task in current for alarm in task.alarms}
        for task in restored:
            for alarm in task.alarms:
                if alarm.id in statuses:
                    alarm.status = statuses[alarm.id]

    def _persist_tasks(self, document: Document, before: list[Task]) -> None:
        if document.tasks == before:
            return
        self._store.save(document)
        self._history.push(before)

    def _find(self, document: Document, task_id: str) -> Task:
        for task in document.tasks:
            if task.id == task_id:
                return task
        raise NotFoundError(f"Task not found: {task_id}")

    def _find_subtask(self, document: Document, task_id: str, subtask_id: str) -> Subtask:
        task = self._find(document, task_id)
        for subtask in task.subtasks:
            if subtask.id == subtask_id:
                return subtask
        raise NotFoundError(f"Subtask not found: {subtask_id}")
