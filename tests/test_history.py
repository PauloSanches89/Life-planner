from datetime import date, datetime

import pytest

from planner.models import EmptyHistory, NotFoundError
from planner.service import PlannerService
from planner.store import Store
from tests.conftest import OFFSET


def test_undo_redo_create_and_a_new_edit_clears_redo(service):
    task = service.create_task()
    service.undo()
    with pytest.raises(NotFoundError):
        service.get_task(task.id)

    service.redo()
    assert service.get_task(task.id).id == task.id

    service.undo()
    service.create_task()
    with pytest.raises(EmptyHistory):
        service.redo()
    assert service.history_state().can_redo is False


def test_undo_delete_restores_subtasks_links_and_alarms(service):
    task = service.create_task()
    service.update_task(task.id, title="Alpha", description="Body")
    subtask = service.add_subtask(task.id, "line")
    link = service.add_link(task.id, "https://app.diagrams.net/x", "drawio", "Board")
    alarm = service.add_alarm(task.id, datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET))
    service.delete_task(task.id)
    assert service.list_tasks_on(date(2026, 10, 28)) == []

    service.undo()
    restored = service.get_task(task.id)
    assert restored.title == "Alpha"
    assert restored.description == "Body"
    assert restored.subtasks == [subtask]
    assert restored.links == [link]
    assert restored.alarms == [alarm]

    service.redo()
    with pytest.raises(NotFoundError):
        service.get_task(task.id)


def test_undo_redo_subtask_add_and_delete(service):
    task = service.create_task()
    subtask = service.add_subtask(task.id, "one")

    service.undo()
    assert service.get_task(task.id).subtasks == []

    service.redo()
    assert service.get_task(task.id).subtasks == [subtask]

    service.delete_subtask(task.id, subtask.id)
    assert service.get_task(task.id).subtasks == []

    service.undo()
    assert service.get_task(task.id).subtasks == [subtask]


def test_selected_day_is_not_an_undo_step(service):
    service.set_selected_day(date(2026, 12, 1))
    assert service.history_state().can_undo is False
    with pytest.raises(EmptyHistory):
        service.undo()

    task = service.create_task()
    service.set_selected_day(date(2026, 12, 2))
    service.undo()

    assert service.get_selected_day() == date(2026, 12, 2)
    with pytest.raises(NotFoundError):
        service.get_task(task.id)


def test_history_cap_drops_the_oldest_edit(tmp_path, clock):
    service = PlannerService(Store(tmp_path, clock), clock, history_limit=2)
    first = service.create_task()
    second = service.create_task()
    third = service.create_task()

    service.undo()
    service.undo()
    with pytest.raises(EmptyHistory):
        service.undo()

    assert service.get_task(first.id).id == first.id
    with pytest.raises(NotFoundError):
        service.get_task(second.id)
    with pytest.raises(NotFoundError):
        service.get_task(third.id)
