import asyncio
import random
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


def backoff_delay(attempt: int, retry_after: float | None = None, cap_s: float = 8.0) -> float:
    """Exponential with jitter: min(cap, 0.5·2^(n−1)) + U(0, 0.3).

    The jitter matters — without it, every retrying client in a fleet wakes
    at the same instant and re-creates the spike that caused the failure.
    A provider's own `Retry-After` always wins over our guess.
    """
    if retry_after is not None:
        return max(0.0, retry_after)
    return min(cap_s, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.3)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    retry_after_of: Callable[[Exception], float | None] = lambda _: None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> T:
    """Retries a coroutine factory with jittered backoff. Re-raises the last
    error once the attempts are spent — the caller decides what a total
    failure means."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as e:
            last = e
            if attempt == attempts:
                break
            if on_retry:
                on_retry(attempt, e)
            await asyncio.sleep(backoff_delay(attempt, retry_after_of(e)))
    assert last is not None
    raise last
