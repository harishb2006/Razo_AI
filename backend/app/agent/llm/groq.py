import json

import httpx

from app.agent.llm.base import (
    ChatMessage, ChatResponse, ProviderTimeout, ProviderUnavailable, RateLimited, ToolCall, ToolSpecDict,
)

_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider:
    name = "groq"

    def __init__(self, api_key: str, model: str = "llama-3.1-8b-instant"):
        self.api_key = api_key
        self.model = model

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float
    ) -> ChatResponse:
        if not self.api_key:
            raise ProviderUnavailable("GROQ_API_KEY not configured")

        oa_messages = [
            {"role": m["role"], "content": m.get("content", "")}
            for m in messages if m.get("role") in ("system", "user", "assistant", "tool")
        ]
        body: dict = {"model": self.model, "messages": oa_messages}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"], "parameters": t["parameters"],
                }}
                for t in tools
            ]

        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(_URL, headers=headers, json=body)
        except httpx.TimeoutException:
            raise ProviderTimeout()
        except httpx.TransportError as e:
            raise ProviderUnavailable(str(e))

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimited(retry_after=float(retry_after) if retry_after else None)
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"groq {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"groq {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ChatResponse(text="", provider=self.name)

        message = choices[0].get("message", {})
        raw_calls = message.get("tool_calls") or []
        tool_calls = [
            ToolCall(name=c["function"]["name"], args=json.loads(c["function"].get("arguments") or "{}"))
            for c in raw_calls
        ]
        if tool_calls:
            return ChatResponse(tool_calls=tool_calls, provider=self.name)

        return ChatResponse(text=message.get("content") or "", provider=self.name)
