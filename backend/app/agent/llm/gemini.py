import httpx

from app.agent.llm.base import (
    ChatMessage, ChatResponse, ProviderTimeout, ProviderUnavailable, RateLimited, ToolCall, ToolSpecDict,
)

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float
    ) -> ChatResponse:
        if not self.api_key:
            raise ProviderUnavailable("GEMINI_API_KEY not configured")

        system = [m["content"] for m in messages if m.get("role") == "system"]
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
            if m.get("role") in ("user", "assistant", "tool") and m.get("content")
        ]
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system[0]}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ]}]

        url = _URL.format(model=self.model)
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, params={"key": self.api_key}, json=body)
        except httpx.TimeoutException:
            raise ProviderTimeout()
        except httpx.TransportError as e:
            raise ProviderUnavailable(str(e))

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            raise RateLimited(retry_after=float(retry_after) if retry_after else None)
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"gemini {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderUnavailable(f"gemini {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return ChatResponse(text="", provider=self.name)

        parts = candidates[0].get("content", {}).get("parts", [])
        tool_calls = [
            ToolCall(name=p["functionCall"]["name"], args=p["functionCall"].get("args", {}))
            for p in parts if "functionCall" in p
        ]
        if tool_calls:
            return ChatResponse(tool_calls=tool_calls, provider=self.name)

        text = "".join(p.get("text", "") for p in parts)
        return ChatResponse(text=text, provider=self.name)
