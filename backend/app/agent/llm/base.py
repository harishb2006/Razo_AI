from typing import Protocol, TypedDict

from pydantic import BaseModel


class ChatMessage(TypedDict, total=False):
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_name: str
    tool_args: dict
    # Opaque provider state belonging to the tool call this message answers.
    # Gemini 3 rejects a history whose functionCall parts have lost it.
    tool_signature: str


class ToolSpecDict(TypedDict):
    name: str
    description: str
    parameters: dict  # JSON Schema


class ToolCall(BaseModel):
    name: str
    args: dict
    signature: str | None = None  # provider-specific; echoed back on the next turn


class ChatResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = []
    provider: str


class ProviderError(Exception):
    """Base for all provider-level failures the router knows how to handle."""


class RateLimited(ProviderError):
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__("rate limited")


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    """Not configured, or a fatal (non-retryable) error — move to the next provider."""


class LLMUnavailable(Exception):
    """Every provider in the failover chain failed. The orchestrator's signal
    to fall back to degraded (non-LLM) mode."""


class LLMProvider(Protocol):
    name: str

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float
    ) -> ChatResponse: ...
