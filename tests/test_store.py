import os
from datetime import date, datetime

import pytest

from planner.clock import SystemClock
from planner.models import StoreError
from planner.service import PlannerService
from planner.store import Store, default_store_root
from tests.conftest import OFFSET


def test_roundtrip_keeps_profile_day_and_task_graph(store, clock):
    service = PlannerService(store, clock)
    service.set_selected_day(date(2026, 10, 30))
    task = service.create_task()
    service.update_task(
        task.id,
        title="Plan",
        description="Notes",
        end_date=date(2026, 11, 1),
    )
    service.add_subtask(task.id, "Step")
    service.add_link(task.id, "https://app.diagrams.net/x", "drawio", "Board")
    service.add_alarm(task.id, datetime(2026, 10, 30, 9, 30, tzinfo=OFFSET))

    reloaded = PlannerService(Store(store.root, clock), clock)

    assert reloaded.current_user() == service.current_user()
    assert reloaded.get_selected_day() == date(2026, 10, 30)
    assert reloaded.get_task(task.id) == service.get_task(task.id)
    assert not (store.root / "store.json.tmp").exists()


def test_anonymous_profile_is_stable_across_reloads(tmp_path, clock):
    first = PlannerService(Store(tmp_path, clock), clock)
    user = first.current_user()

    assert user.display_name == "Local user"
    assert user.provider == "anonymous"
    assert user.avatar_url is None

    second = PlannerService(Store(tmp_path, clock), clock)
    assert second.current_user() == user


def test_failed_replace_keeps_the_previous_file(store, clock, monkeypatch):
    service = PlannerService(store, clock)
    service.create_task()
    original = store.path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        service.create_task()

    assert store.path.read_text(encoding="utf-8") == original
    assert len(Store(store.root, clock).load().tasks) == 1


def test_corrupt_json_raises(store):
    store.root.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{", encoding="utf-8")
    with pytest.raises(StoreError):
        store.load()


def test_default_root_is_repo_userdata():
    root = default_store_root()
    assert root.name == "userdata"
    assert (root.parent / "pyproject.toml").is_file()


def test_system_clock_reads_an_aware_local_time():
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None
