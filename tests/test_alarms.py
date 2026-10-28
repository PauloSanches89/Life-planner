import time
from datetime import datetime

import pytest

from planner.alarms import AlarmScheduler
from planner.models import AlarmStatus, InvalidAlarm
from tests.conftest import OFFSET


def test_startup_marks_past_alarms_missed_without_an_undo_step(service, clock):
    task = service.create_task()
    alarm = service.add_alarm(task.id, datetime(2026, 10, 28, 7, 0, tzinfo=OFFSET))
    scheduler = AlarmScheduler(service, clock)

    scheduler.collect_startup_misses()

    stored = service.get_task(task.id).alarms[0]
    assert stored.id == alarm.id
    assert stored.status is AlarmStatus.MISSED
    events = service.drain_alarm_events()
    assert [(event.alarm_id, event.status, event.task_title) for event in events] == [
        (alarm.id, AlarmStatus.MISSED, "New task")
    ]
    assert service.drain_alarm_events() == []

    service.undo()
    assert service.get_task(task.id).alarms == []


def test_tick_fires_due_alarms_once(service, clock):
    task = service.create_task()
    fires_at = datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET)
    alarm = service.add_alarm(task.id, fires_at)
    scheduler = AlarmScheduler(service, clock)

    scheduler.collect_startup_misses()
    assert service.get_task(task.id).alarms[0].status is AlarmStatus.SCHEDULED
    assert service.drain_alarm_events() == []

    clock.set(fires_at)
    scheduler.tick()
    assert service.get_task(task.id).alarms[0].status is AlarmStatus.FIRED
    events = service.drain_alarm_events()
    assert [event.status for event in events] == [AlarmStatus.FIRED]
    assert events[0].alarm_id == alarm.id

    scheduler.tick()
    assert service.drain_alarm_events() == []


def test_missed_alarm_is_not_fired_later(service, clock):
    task = service.create_task()
    service.add_alarm(task.id, datetime(2026, 10, 28, 7, 0, tzinfo=OFFSET))
    scheduler = AlarmScheduler(service, clock)
    scheduler.collect_startup_misses()
    service.drain_alarm_events()

    clock.set(datetime(2026, 10, 28, 12, 0, tzinfo=OFFSET))
    scheduler.tick()

    assert service.get_task(task.id).alarms[0].status is AlarmStatus.MISSED
    assert service.drain_alarm_events() == []


def test_undo_does_not_rewind_a_fired_alarm(service, clock):
    task = service.create_task()
    fires_at = datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET)
    service.add_alarm(task.id, fires_at)
    service.update_task(task.id, title="Renamed")
    clock.set(fires_at)
    AlarmScheduler(service, clock).tick()

    service.undo()

    restored = service.get_task(task.id)
    assert restored.title == "New task"
    assert restored.alarms[0].status is AlarmStatus.FIRED

    service.redo()
    assert service.get_task(task.id).title == "Renamed"
    assert service.get_task(task.id).alarms[0].status is AlarmStatus.FIRED


def test_naive_alarm_time_is_rejected(service):
    task = service.create_task()
    with pytest.raises(InvalidAlarm):
        service.add_alarm(task.id, datetime(2026, 10, 28, 9, 0))
    assert service.get_task(task.id).alarms == []


def test_scheduler_thread_fires_after_the_clock_advances(service, clock):
    task = service.create_task()
    service.add_alarm(task.id, datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET))
    scheduler = AlarmScheduler(service, clock, interval=0.05)
    scheduler.start()
    try:
        assert service.get_task(task.id).alarms[0].status is AlarmStatus.SCHEDULED
        clock.set(datetime(2026, 10, 28, 9, 0, tzinfo=OFFSET))
        deadline = time.time() + 3
        events = []
        while time.time() < deadline:
            events = service.drain_alarm_events()
            if events:
                break
            time.sleep(0.05)
        assert [event.status for event in events] == [AlarmStatus.FIRED]
    finally:
        scheduler.stop()
