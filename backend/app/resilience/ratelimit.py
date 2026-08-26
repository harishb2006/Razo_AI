import asyncio
import time
from typing import Callable


class TokenBucket:
    """Shapes our *outbound* call rate to sit just under a provider's free
    tier, so we throttle ourselves before the provider throttles us. That is
    what keeps a 24-session eval run from burning the daily quota — and it
    turns a hard 429 into a short wait."""

    def __init__(
        self,
        rate_per_minute: float,
        burst: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.rate_per_second = rate_per_minute / 60.0
        self.capacity = burst if burst is not None else max(1, int(rate_per_minute))
        self._tokens = float(self.capacity)
        self._clock = clock
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)

    def try_acquire(self) -> bool:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def delay_until_available(self) -> float:
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        return (1.0 - self._tokens) / self.rate_per_second

    async def acquire(self) -> float:
        """Waits for a token and returns how long it waited, so the caller
        can record self-throttling honestly in the metrics."""
        async with self._lock:
            waited = 0.0
            while True:
                delay = self.delay_until_available()
                if delay <= 0:
                    self._tokens -= 1.0
                    return waited
                waited += delay
                await asyncio.sleep(delay)
