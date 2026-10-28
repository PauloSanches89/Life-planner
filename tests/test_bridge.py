from datetime import datetime

from planner.alarms import AlarmScheduler
from planner.bridge import JsApi
from planner.service import PlannerService
from planner.store import Store
from tests.conftest import OFFSET


def build(clock, tmp_path, opened=None):
    service = PlannerService(Store(tmp_path, clock), clock)
    opener = opened.append if opened is not None else (lambda url: None)
    return JsApi(service, clock, open_url=opener), service


def test_create_task_lands_on_the_locked_day(clock, tmp_path):
    api, _service = build(clock, tmp_path)
    api.set_selected_day("2026-11-02")

    result = api.create_task()

    assert result["ok"] is True
    assert result["screen"]["selected_day"] == "2026-11-02"
    assert result["screen"]["today"] == "2026-10-28"
    assert result["screen"]["user"]["display_name"] == "Local user"
    assert result["screen"]["user"]["avatar_url"] is None
    assert result["task"]["start"] == "2026-11-02"
    assert result["task"]["end"] == "2026-11-02"
    assert result["task"]["title"] == "New task"
    assert [item["id"] for item in result["screen"]["tasks"]] == [result["task"]["id"]]


def test_span_is_one_id_on_each_covered_day(clock, tmp_path):
    api, _service = build(clock, tmp_path)
    task_id = api.create_task()["task"]["id"]
    api.update_task(task_id, None, None, "2026-10-28", "2026-10-29")

    first = api.set_selected_day("2026-10-28", task_id)
    second = api.set_selected_day("2026-10-29", task_id)
    outside = api.set_selected_day("2026-10-27", task_id)

    assert [item["id"] for item in first["screen"]["tasks"]] == [task_id]
    assert [item["id"] for item in second["screen"]["tasks"]] == [task_id]
    assert first["task"]["id"] == second["task"]["id"] == task_id
    assert outside["screen"]["tasks"] == []
    assert outside["task"] is None


def test_pasted_links_keep_kind_and_default_label(clock, tmp_path):
    api, _service = build(clock, tmp_path)
    task_id = api.create_task()["task"]["id"]

    api.add_link(task_id, "https://app.diagrams.net/x", "drawio", "  ")
    api.add_link(task_id, "https://example.com/plan", "generic", "Notes")
    links = api.snapshot(task_id)["screen"]["tasks"][0]["links"]

    assert [(link["kind"], link["label"], link["url"]) for link in links] == [
        ("drawio", "draw.io", "https://app.diagrams.net/x"),
        ("generic", "Notes", "https://example.com/plan"),
    ]


def test_invalid_span_returns_an_error(clock, tmp_path):
    api, _service = build(clock, tmp_path)
    task_id = api.create_task()["task"]["id"]

    result = api.update_task(task_id, None, None, "2026-10-28", "2026-10-27")

    assert result["ok"] is False
    assert "end date" in result["error"].lower()
    assert api.snapshot(task_id)["task"]["end"] == "2026-10-28"


def test_alarm_offset_fire_and_miss(clock, tmp_path):
    api, service = build(clock, tmp_path)
    task_id = api.create_task()["task"]["id"]
    added = api.add_alarm(task_id, "2026-10-28T09:30")
    scheduler = AlarmScheduler(service, clock)

    assert added["task"]["alarms"][0]["fires_at"] == "2026-10-28T09:30:00+02:00"
    assert added["task"]["alarms"][0]["status"] == "scheduled"
    assert api.drain_alarm_events()["events"] == []

    scheduler.tick(datetime(2026, 10, 28, 9, 30, tzinfo=OFFSET))
    fired = api.drain_alarm_events()["events"]
    assert [(event["status"], event["task_title"]) for event in fired] == [("fired", "New task")]
    assert api.snapshot(task_id)["task"]["alarms"][0]["status"] == "fired"

    missed_task = api.create_task()["task"]["id"]
    api.add_alarm(missed_task, "2026-10-28T07:00")
    scheduler.collect_startup_misses()
    missed = api.drain_alarm_events()["events"]
    assert [event["status"] for event in missed] == ["missed"]
    assert api.snapshot(missed_task)["task"]["alarms"][0]["status"] == "missed"


def test_undo_and_redo_flags_follow_task_and_subtask_edits(clock, tmp_path):
    api, _service = build(clock, tmp_path)
    assert api.snapshot()["screen"]["can_undo"] is False

    task_id = api.create_task()["task"]["id"]
    assert api.snapshot()["screen"]["can_undo"] is True
    subtask_id = api.add_subtask(task_id, "line")["task"]["subtasks"][0]["id"]
    api.delete_subtask(task_id, subtask_id)
    restored_subtask = api.undo(task_id)
    assert restored_subtask["screen"]["can_redo"] is True
    assert restored_subtask["task"]["subtasks"][0]["text"] == "line"

    api.delete_task(task_id)
    assert api.snapshot()["screen"]["tasks"] == []
    restored = api.undo(task_id)
    assert restored["task"]["id"] == task_id
    assert restored["screen"]["can_redo"] is True
    assert api.redo(task_id)["task"] is None
    assert api.snapshot()["screen"]["can_undo"] is True


def test_open_link_allows_only_http_urls(clock, tmp_path):
    opened = []
    api, _service = build(clock, tmp_path, opened)

    assert api.open_link("javascript:alert(1)")["ok"] is False
    assert api.open_link("file:///C:/secret.txt")["ok"] is False
    assert opened == []
    assert api.open_link("https://app.diagrams.net/x")["ok"] is True
    assert opened == ["https://app.diagrams.net/x"]
