import json
from datetime import date, datetime

import pytest

from planner.models import AlarmStatus, InvalidLink, InvalidSpan, LinkKind, NotFoundError
from tests.conftest import OFFSET


def test_selected_day_defaults_to_the_clock(service):
    assert service.get_selected_day() == date(2026, 10, 28)
    assert service.today() == date(2026, 10, 28)


def test_create_task_uses_the_locked_day(service):
    service.set_selected_day(date(2026, 11, 2))
    task = service.create_task()

    assert task.title == "New task"
    assert task.description == ""
    assert task.start_date == date(2026, 11, 2)
    assert task.end_date == date(2026, 11, 2)
    assert [item.id for item in service.list_tasks_on(date(2026, 11, 2))] == [task.id]
    assert service.list_tasks_on(date(2026, 11, 1)) == []


def test_span_is_one_record_on_every_covered_day(service, store):
    task = service.create_task()
    service.update_task(task.id, start_date=date(2026, 10, 27), end_date=date(2026, 10, 29))

    seen = []
    for day_number in (26, 27, 28, 29, 30):
        found = service.list_tasks_on(date(2026, 10, day_number))
        if day_number in (27, 28, 29):
            assert [item.id for item in found] == [task.id]
            seen.append(found[0].id)
        else:
            assert found == []

    assert seen == [task.id, task.id, task.id]
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["tasks"]] == [task.id]


def test_span_edit_drops_the_task_from_the_locked_day_only(service):
    service.set_selected_day(date(2026, 10, 28))
    task = service.create_task()
    service.update_task(task.id, start_date=date(2026, 10, 30), end_date=date(2026, 10, 31))

    assert service.list_tasks_on(date(2026, 10, 28)) == []
    assert service.get_task(task.id).start_date == date(2026, 10, 30)
    assert [item.id for item in service.list_tasks_on(date(2026, 10, 30))] == [task.id]
    assert [item.id for item in service.list_tasks_on(date(2026, 10, 31))] == [task.id]


def test_invalid_span_does_not_change_the_task(service):
    task = service.create_task()

    with pytest.raises(InvalidSpan):
        service.update_task(task.id, end_date=date(2026, 10, 27))

    assert service.get_task(task.id).end_date == date(2026, 10, 28)
    service.undo()
    with pytest.raises(NotFoundError):
        service.get_task(task.id)


def test_tasks_stay_in_creation_order(service):
    first = service.create_task()
    second = service.create_task()
    service.update_task(first.id, title="First")
    service.update_task(second.id, title="Second")

    assert [task.title for task in service.list_tasks_on(date(2026, 10, 28))] == ["First", "Second"]


def test_subtask_link_and_alarm_crud(service):
    task = service.create_task()
    service.update_task(task.id, title="Alpha", description="Notes")
    first = service.add_subtask(task.id, "a")
    second = service.add_subtask(task.id, "b")
    service.update_subtask(task.id, first.id, "A")
    link = service.add_link(task.id, "https://app.diagrams.net/x", "drawio", "Board")
    generic = service.add_link(task.id, "https://example.com/plan", "generic", "Notes")
    alarm = service.add_alarm(task.id, datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET))

    stored = service.get_task(task.id)
    assert stored.title == "Alpha"
    assert stored.description == "Notes"
    assert [(item.id, item.text, item.position) for item in stored.subtasks] == [
        (first.id, "A", 0),
        (second.id, "b", 1),
    ]
    assert stored.links == [link, generic]
    assert link.kind is LinkKind.DRAWIO
    assert generic.kind is LinkKind.GENERIC
    assert stored.alarms == [alarm]
    assert alarm.status is AlarmStatus.SCHEDULED

    service.delete_subtask(task.id, first.id)
    service.delete_link(task.id, generic.id)
    service.delete_alarm(task.id, alarm.id)

    stored = service.get_task(task.id)
    assert [(item.id, item.position) for item in stored.subtasks] == [(second.id, 0)]
    assert stored.links == [link]
    assert stored.alarms == []


def test_unknown_link_kind_and_blank_url_are_rejected(service):
    task = service.create_task()

    with pytest.raises(InvalidLink):
        service.add_link(task.id, "https://example.com", "notion", "N")
    with pytest.raises(InvalidLink):
        service.add_link(task.id, "  ", "generic", "N")

    assert service.get_task(task.id).links == []


def test_missing_task_raises(service):
    with pytest.raises(NotFoundError):
        service.get_task("missing")
