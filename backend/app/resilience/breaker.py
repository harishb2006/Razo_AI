import time
from typing import Callable

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    """Stops hammering a provider that is already failing.

    closed  — normal; failures inside the rolling window are counted.
    open    — short-circuited; calls are refused without being attempted.
    half_open — after the cool-off, exactly one probe is allowed through.
                It closes the circuit if it succeeds and re-opens it if not.

    In-process and per-provider, which is all a single-process deployment
    needs — no Redis, in keeping with the zero-cost constraint.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        window_s: float = 60.0,
        cool_off_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_s = window_s
        self.cool_off_s = cool_off_s
        self._clock = clock
        self._failures: list[float] = []
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return CLOSED
        if self._clock() - self._opened_at >= self.cool_off_s:
            return HALF_OPEN
        return OPEN

    def allows(self) -> bool:
        """A half-open circuit lets exactly one probe through; everything
        else waits, so a recovering provider is not stampeded."""
        state = self.state
        if state == CLOSED:
            return True
        if state == OPEN:
            return False
        if self._probing:
            return False
        self._probing = True
        return True

    def record_success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probing = False

    def record_failure(self) -> None:
        now = self._clock()
        self._probing = False

        if self._opened_at is not None:
            # A failed half-open probe restarts the cool-off rather than
            # letting probes through every call.
            self._opened_at = now
            return

        self._failures = [t for t in self._failures if now - t < self.window_s]
        self._failures.append(now)
        if len(self._failures) >= self.failure_threshold:
            self._opened_at = now
            self._failures.clear()

    def reset(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probing = False
