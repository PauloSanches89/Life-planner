"""Clock port. The system clock reads the operating-system local time."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware local time."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()
