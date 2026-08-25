import asyncio
import random

from app.agent.llm.base import (
    ChatMessage, ChatResponse, LLMUnavailable, ProviderTimeout, ProviderUnavailable, RateLimited, ToolSpecDict,
)
from app.agent.llm.echo import EchoProvider
from app.agent.llm.gemini import GeminiProvider
from app.agent.llm.groq import GroqProvider
from app.config import settings


def _backoff(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None:
        return retry_after
    return min(8.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.3)


class LLMRouter:
    """Gemini -> Groq -> echo, with retry+backoff per provider and immediate
    failover on a fatal/unconfigured provider. `echo` never raises, so this
    only surfaces LLMUnavailable if the chain itself is misconfigured."""

    def __init__(self):
        self._providers = {
            "gemini": GeminiProvider(settings.gemini_api_key),
            "groq": GroqProvider(settings.groq_api_key),
            "echo": EchoProvider(),
        }
        self._chain = [p.strip() for p in settings.llm_provider_chain.split(",") if p.strip()]

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float | None = None
    ) -> ChatResponse:
        timeout_s = timeout_s or settings.llm_timeout_s
        last_error: Exception | None = None

        for name in self._chain:
            provider = self._providers.get(name)
            if provider is None:
                continue
            max_attempts = 3 if name != "echo" else 1
            for attempt in range(1, max_attempts + 1):
                try:
                    return await provider.chat(messages, tools, timeout_s)
                except ProviderUnavailable as e:
                    last_error = e
                    break  # no retry — next provider in the chain
                except RateLimited as e:
                    last_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(_backoff(attempt, e.retry_after))
                except ProviderTimeout as e:
                    last_error = e
                    if attempt < max_attempts:
                        await asyncio.sleep(_backoff(attempt))

        raise LLMUnavailable(str(last_error) if last_error else "no provider configured")


llm_router = LLMRouter()
