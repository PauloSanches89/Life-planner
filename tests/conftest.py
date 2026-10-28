from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from planner.service import PlannerService
from planner.store import Store

OFFSET = timezone(timedelta(hours=2))


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 28, 8, 0, tzinfo=OFFSET))


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> Store:
    return Store(tmp_path, clock)


@pytest.fixture
def service(store: Store, clock: FakeClock) -> PlannerService:
    return PlannerService(store, clock)
