import asyncio
import logging
import time
from datetime import datetime, timezone

from ulid import ULID

from app.agent.llm.base import (
    ChatMessage, ChatResponse, LLMUnavailable, ProviderTimeout, ProviderUnavailable, RateLimited, ToolSpecDict,
)
from app.agent.llm.echo import EchoProvider
from app.agent.llm.gemini import GeminiProvider
from app.agent.llm.groq import GroqProvider
from app.config import settings
from app.resilience.breaker import CircuitBreaker
from app.resilience.ratelimit import TokenBucket
from app.resilience.retry import backoff_delay

log = logging.getLogger(__name__)

# `echo` needs neither a breaker nor a budget: it is in-process, deterministic
# and cannot fail, which is exactly why it is the last link in the chain.
_LOCAL_PROVIDERS = frozenset({"echo"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LLMRouter:
    """Gemini -> Groq -> echo.

    Per provider: a token bucket that keeps us under the free tier, a circuit
    breaker that stops hammering something already failing, and retry with
    jittered backoff. Because `echo` is local and always succeeds, the chain
    as a whole cannot raise — which is what makes 'no unhandled crash' true
    even with no network at all.
    """

    def __init__(self):
        # OFFLINE_MODE has to mean offline. Withholding the keys (rather than
        # shortening the chain) keeps the failover path itself intact and
        # exercisable, while making every remote provider report itself
        # unconfigured — so it is skipped without ever touching the network.
        remote_keys_allowed = not settings.offline_mode
        self._providers = {
            "gemini": GeminiProvider(settings.gemini_api_key if remote_keys_allowed else ""),
            "groq": GroqProvider(settings.groq_api_key if remote_keys_allowed else ""),
            "echo": EchoProvider(),
        }
        self._chain = [p.strip() for p in settings.llm_provider_chain.split(",") if p.strip()]
        self._breakers = {
            name: CircuitBreaker(
                name,
                failure_threshold=settings.breaker_failure_threshold,
                window_s=settings.breaker_window_s,
                cool_off_s=settings.breaker_cool_off_s,
            )
            for name in self._providers
            if name not in _LOCAL_PROVIDERS
        }
        self._buckets = {
            name: TokenBucket(settings.llm_rate_limit_per_minute)
            for name in self._providers
            if name not in _LOCAL_PROVIDERS
        }

    def breaker_states(self) -> dict[str, str]:
        return {name: b.state for name, b in self._breakers.items()}

    def reset(self) -> None:
        for breaker in self._breakers.values():
            breaker.reset()

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpecDict],
        timeout_s: float | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> ChatResponse:
        timeout_s = timeout_s or settings.llm_timeout_s
        last_error: Exception | None = None
        used_fallback = False

        for name in self._chain:
            provider = self._providers.get(name)
            if provider is None:
                continue

            breaker = self._breakers.get(name)
            if breaker is not None and not breaker.allows():
                await self._record(session_id, trace_id, name, 1, "breaker_open", 0, "circuit open")
                used_fallback = True
                continue

            # A provider with no key configured never reaches the network —
            # ProviderUnavailable fires immediately below. Shaping our rate
            # against a call that costs nothing would just throttle
            # ourselves for no reason.
            configured = bool(getattr(provider, "api_key", True))
            max_attempts = 1 if name in _LOCAL_PROVIDERS or not configured else settings.llm_max_attempts

            for attempt in range(1, max_attempts + 1):
                throttled_ms = 0
                bucket = self._buckets.get(name)
                if bucket is not None and configured:
                    throttled_ms = int(await bucket.acquire() * 1000)

                started = time.monotonic()
                try:
                    response = await provider.chat(messages, tools, timeout_s)
                except ProviderUnavailable as e:
                    last_error = e
                    await self._record(
                        session_id, trace_id, name, attempt, "unavailable",
                        self._ms(started), str(e), throttled_ms,
                    )
                    if breaker is not None:
                        breaker.record_failure()
                    break  # fatal or unconfigured — no retry, next provider
                except RateLimited as e:
                    last_error = e
                    await self._record(
                        session_id, trace_id, name, attempt, "rate_limited",
                        self._ms(started), "429", throttled_ms,
                    )
                    if breaker is not None:
                        breaker.record_failure()
                    if attempt < max_attempts:
                        await asyncio.sleep(backoff_delay(attempt, e.retry_after))
                except ProviderTimeout as e:
                    last_error = e
                    await self._record(
                        session_id, trace_id, name, attempt, "timeout",
                        self._ms(started), "timeout", throttled_ms,
                    )
                    if breaker is not None:
                        breaker.record_failure()
                    if attempt < max_attempts:
                        await asyncio.sleep(backoff_delay(attempt))
                else:
                    await self._record(
                        session_id, trace_id, name, attempt, "ok",
                        self._ms(started), None, throttled_ms,
                    )
                    if breaker is not None:
                        breaker.record_success()
                    if used_fallback:
                        await self._audit_fallback(session_id, trace_id, name)
                    return response

            used_fallback = True

        raise LLMUnavailable(str(last_error) if last_error else "no provider configured")

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    async def _record(
        session_id: str | None, trace_id: str | None, provider: str, attempt: int,
        status: str, latency_ms: int, error_code: str | None = None, throttled_ms: int = 0,
    ) -> None:
        """Best-effort: a failure to record telemetry must never be the thing
        that breaks a buyer's turn."""
        from app.db.documents import LLMCall

        try:
            await LLMCall(
                id=str(ULID()), session_id=session_id, trace_id=trace_id, provider=provider,
                attempt=attempt, status=status, latency_ms=latency_ms, error_code=error_code,
                throttled_ms=throttled_ms, created_at=_now(),
            ).insert()
        except Exception:
            log.debug("Could not record llm_call for %s", provider, exc_info=True)

    @staticmethod
    async def _audit_fallback(session_id: str | None, trace_id: str | None, provider: str) -> None:
        from app.audit.service import audit_safe

        await audit_safe(
            actor="agent", action="llm.fallback", session_id=session_id, trace_id=trace_id,
            output={"provider": provider},
            reason=f"The primary AI provider was unavailable, so '{provider}' answered this turn instead. "
                   "The rulebook is unaffected by which provider replied.",
            outcome="degraded",
        )


llm_router = LLMRouter()
