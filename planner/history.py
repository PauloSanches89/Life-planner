"""Session-only undo stack of task-list snapshots. Navigation is not recorded."""

from __future__ import annotations

import copy
from dataclasses import dataclass

from planner.models import EmptyHistory, Task


@dataclass(frozen=True)
class HistoryState:
    can_undo: bool
    can_redo: bool


class History:
    def __init__(self, limit: int = 100) -> None:
        self._undo: list[list[Task]] = []
        self._redo: list[list[Task]] = []
        self._limit = limit

    def state(self) -> HistoryState:
        return HistoryState(can_undo=bool(self._undo), can_redo=bool(self._redo))

    def push(self, tasks: list[Task]) -> None:
        self._undo.append(copy.deepcopy(tasks))
        if len(self._undo) > self._limit:
            del self._undo[0]
        self._redo.clear()

    def preview_undo(self) -> list[Task]:
        if not self._undo:
            raise EmptyHistory("Nothing to undo")
        return copy.deepcopy(self._undo[-1])

    def commit_undo(self, current: list[Task]) -> None:
        if not self._undo:
            raise EmptyHistory("Nothing to undo")
        self._undo.pop()
        self._redo.append(copy.deepcopy(current))

    def preview_redo(self) -> list[Task]:
        if not self._redo:
            raise EmptyHistory("Nothing to redo")
        return copy.deepcopy(self._redo[-1])

    def commit_redo(self, current: list[Task]) -> None:
        if not self._redo:
            raise EmptyHistory("Nothing to redo")
        self._redo.pop()
        self._undo.append(copy.deepcopy(current))
        if len(self._undo) > self._limit:
            del self._undo[0]
