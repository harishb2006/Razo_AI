from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Deterministic clock for tests — same intent + policy + clock must
    produce byte-identical findings."""

    def __init__(self, at: datetime):
        self._at = at

    def now(self) -> datetime:
        return self._at
