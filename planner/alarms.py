"""In-process alarm scheduler. It runs only while the app process is alive."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Protocol

from planner.clock import Clock
from planner.models import AlarmStatus


class AlarmBook(Protocol):
    def transition_due_alarms(self, now: datetime, outcome: AlarmStatus) -> None:
        """Mark due scheduled alarms and queue events. Does not record undo."""


class AlarmScheduler:
    def __init__(self, book: AlarmBook, clock: Clock, interval: float = 1.0) -> None:
        self._book = book
        self._clock = clock
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()

    def collect_startup_misses(self, now: datetime | None = None) -> None:
        """Alarms that came due while the process was closed are missed."""
        moment = self._clock.now() if now is None else now
        self._book.transition_due_alarms(moment, AlarmStatus.MISSED)

    def tick(self, now: datetime | None = None) -> None:
        """Alarms that come due while the scheduler is running are fired."""
        moment = self._clock.now() if now is None else now
        self._book.transition_due_alarms(moment, AlarmStatus.FIRED)

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self.collect_startup_misses()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="alarm-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.tick()
