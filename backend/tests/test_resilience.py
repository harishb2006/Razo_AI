"""Fault injection. The required handled failure (F1) is an LLM provider
rate-limiting us, and the claim being tested is narrow and important: the
buyer never sees a crash, and degrading the AI never degrades the rulebook."""
import pytest

from app.agent.llm.base import (
    ChatResponse, LLMUnavailable, ProviderTimeout, ProviderUnavailable, RateLimited,
)
from app.agent.llm.router import LLMRouter
from app.resilience.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from app.resilience.ratelimit import TokenBucket
from app.resilience.retry import backoff_delay, retry_async


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class ScriptedProvider:
    """Raises whatever the script says, in order, then succeeds."""

    def __init__(self, name: str, script: list[Exception | None]):
        self.name = name
        self._script = list(script)
        self.calls = 0

    async def chat(self, messages, tools, timeout_s):
        self.calls += 1
        failure = self._script.pop(0) if self._script else None
        if failure is not None:
            raise failure
        return ChatResponse(text=f"reply from {self.name}", provider=self.name)


def router_with(monkeypatch, **providers) -> LLMRouter:
    """Builds a router whose providers are fully under the test's control.
    The real Groq provider reports itself unavailable without an API key, so
    tests that care about tier-2 behaviour have to supply a working one
    rather than depend on the environment."""
    router = LLMRouter()
    providers.setdefault("groq", ScriptedProvider("groq", []))
    for name, provider in providers.items():
        monkeypatch.setitem(router._providers, name, provider)
    return router


# --- circuit breaker ---------------------------------------------------------

def test_breaker_opens_after_the_failure_threshold():
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=3, window_s=60, cool_off_s=30, clock=clock)

    assert breaker.state == CLOSED
    for _ in range(3):
        breaker.record_failure()

    assert breaker.state == OPEN
    assert breaker.allows() is False


def test_failures_outside_the_window_do_not_accumulate():
    """A provider that fails once an hour is not a broken provider."""
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=3, window_s=60, cool_off_s=30, clock=clock)

    breaker.record_failure()
    clock.advance(61)
    breaker.record_failure()
    clock.advance(61)
    breaker.record_failure()

    assert breaker.state == CLOSED


def test_breaker_half_opens_after_the_cool_off_and_allows_one_probe():
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=2, window_s=60, cool_off_s=30, clock=clock)
    breaker.record_failure()
    breaker.record_failure()

    clock.advance(30)
    assert breaker.state == HALF_OPEN
    assert breaker.allows() is True   # the single probe
    assert breaker.allows() is False  # everything else waits


def test_a_successful_probe_closes_the_breaker():
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=2, window_s=60, cool_off_s=30, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(30)
    breaker.allows()

    breaker.record_success()

    assert breaker.state == CLOSED
    assert breaker.allows() is True


def test_a_failed_probe_restarts_the_cool_off():
    clock = FakeClock()
    breaker = CircuitBreaker("p", failure_threshold=2, window_s=60, cool_off_s=30, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    clock.advance(30)
    breaker.allows()

    breaker.record_failure()

    assert breaker.state == OPEN
    clock.advance(29)
    assert breaker.state == OPEN


# --- backoff -----------------------------------------------------------------

def test_backoff_grows_and_stays_capped():
    assert 0.5 <= backoff_delay(1) <= 0.8
    assert 1.0 <= backoff_delay(2) <= 1.3
    assert backoff_delay(20) <= 8.3


def test_backoff_honours_retry_after():
    assert backoff_delay(1, retry_after=4.2) == 4.2


@pytest.mark.asyncio
async def test_retry_gives_up_and_reraises_the_last_error():
    attempts = 0

    async def always_fails():
        nonlocal attempts
        attempts += 1
        raise RateLimited(retry_after=0)

    with pytest.raises(RateLimited):
        await retry_async(always_fails, attempts=3, retry_on=(RateLimited,))
    assert attempts == 3


# --- token bucket ------------------------------------------------------------

def test_the_bucket_throttles_once_the_burst_is_spent():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=60, burst=2, clock=clock)

    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_the_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(rate_per_minute=60, burst=1, clock=clock)
    bucket.try_acquire()

    clock.advance(1.0)  # 60/min == 1/s

    assert bucket.try_acquire() is True


# --- the router under failure -------------------------------------------------

@pytest.mark.asyncio
async def test_a_429_storm_falls_through_to_the_next_provider(db, monkeypatch):
    """F1, the designated demo failure: the primary rate-limits us, and the
    buyer still gets an answer."""
    primary = ScriptedProvider("gemini", [RateLimited(0), RateLimited(0), RateLimited(0)])
    router = router_with(monkeypatch, gemini=primary)

    response = await router.chat([{"role": "user", "content": "hi"}], [])

    assert response.provider == "groq"
    assert primary.calls == 3  # retried before giving up on it


@pytest.mark.asyncio
async def test_every_provider_failing_falls_back_to_the_local_stand_in(db, monkeypatch):
    router = LLMRouter()
    monkeypatch.setitem(router._providers, "gemini", ScriptedProvider("gemini", [ProviderTimeout()] * 3))
    monkeypatch.setitem(router._providers, "groq", ScriptedProvider("groq", [ProviderTimeout()] * 3))

    response = await router.chat([{"role": "user", "content": "running shoes"}], [])

    assert response.provider == "echo"


@pytest.mark.asyncio
async def test_an_open_breaker_skips_the_provider_without_calling_it(db, monkeypatch):
    primary = ScriptedProvider("gemini", [])
    router = router_with(monkeypatch, gemini=primary)
    for _ in range(router._breakers["gemini"].failure_threshold):
        router._breakers["gemini"].record_failure()

    response = await router.chat([{"role": "user", "content": "hi"}], [])

    assert primary.calls == 0  # not attempted at all
    assert response.provider == "groq"


@pytest.mark.asyncio
async def test_an_unconfigured_provider_is_skipped_without_retrying(db, monkeypatch):
    primary = ScriptedProvider("gemini", [ProviderUnavailable("no key")] * 5)
    router = router_with(monkeypatch, gemini=primary)

    response = await router.chat([{"role": "user", "content": "hi"}], [])

    assert primary.calls == 1  # fatal, so no retry
    assert response.provider == "groq"


@pytest.mark.asyncio
async def test_every_attempt_is_recorded_so_the_fallback_rate_is_measured(db, monkeypatch):
    from app.db.documents import LLMCall

    router = router_with(monkeypatch, gemini=ScriptedProvider("gemini", [RateLimited(0)] * 3))

    await router.chat([{"role": "user", "content": "hi"}], [])

    calls = await LLMCall.find_all().to_list()
    gemini = [c for c in calls if c.provider == "gemini"]
    assert len(gemini) == 3
    assert all(c.status == "rate_limited" for c in gemini)
    assert any(c.provider == "groq" and c.status == "ok" for c in calls)


@pytest.mark.asyncio
async def test_falling_back_is_written_to_the_audit_trail(db, monkeypatch):
    from app.db.documents import AuditEvent

    router = router_with(monkeypatch, gemini=ScriptedProvider("gemini", [RateLimited(0)] * 3))

    await router.chat([{"role": "user", "content": "hi"}], [], session_id="s-1")

    event = await AuditEvent.find_one(AuditEvent.action == "llm.fallback")
    assert event is not None
    assert event.outcome == "degraded"
    assert "groq" in event.reason


@pytest.mark.asyncio
async def test_the_chain_only_raises_when_it_is_misconfigured(db, monkeypatch):
    """With `echo` in the chain nothing can raise; remove it and the router
    reports honestly rather than hanging."""
    router = LLMRouter()
    router._chain = ["gemini"]
    monkeypatch.setitem(router._providers, "gemini", ScriptedProvider("gemini", [ProviderTimeout()] * 3))

    with pytest.raises(LLMUnavailable):
        await router.chat([{"role": "user", "content": "hi"}], [])
