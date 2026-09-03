import json

import httpx

from app.agent.llm.base import (
    ChatMessage, ChatResponse, ProviderTimeout, ProviderUnavailable, RateLimited, ToolCall, ToolSpecDict,
)
from app.config import settings

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Gemini accepts an OpenAPI-flavoured subset of JSON Schema, not the whole of
# it, and rejects the entire request on an unknown key rather than ignoring it.
# Pydantic emits `exclusiveMinimum`/`exclusiveMaximum` for Field(gt=..., lt=...),
# neither of which is in the subset — hence the translation below.
_ALLOWED_SCHEMA_KEYS = frozenset({
    "type", "format", "title", "description", "nullable", "enum", "items",
    "properties", "required", "minimum", "maximum", "minItems", "maxItems",
    "minLength", "maxLength", "pattern", "anyOf", "default",
})


def _sanitize_schema(node):
    """Recursively reduce a JSON Schema to the subset Gemini accepts."""
    if isinstance(node, list):
        return [_sanitize_schema(v) for v in node]
    if not isinstance(node, dict):
        return node

    # Pydantic renders `x: int | None` as anyOf[{int}, {null}]. Gemini has no
    # null type; it spells the same idea `nullable: true` on the real branch.
    variants = node.get("anyOf")
    if isinstance(variants, list):
        concrete = [v for v in variants if isinstance(v, dict) and v.get("type") != "null"]
        if len(concrete) == 1 and len(concrete) < len(variants):
            merged = {k: v for k, v in node.items() if k != "anyOf"}
            merged.update(concrete[0])
            merged["nullable"] = True
            return _sanitize_schema(merged)

    out = {}
    for key, value in node.items():
        if key == "properties":
            # A map of caller-chosen names, not schema keywords: keep every key
            # and sanitise only the sub-schemas underneath it.
            out[key] = {name: _sanitize_schema(sub) for name, sub in value.items()}
        elif key in ("required", "enum"):
            out[key] = value
        elif key == "exclusiveMinimum":
            # Integers are the only numeric type in these tool schemas, so the
            # nearest inclusive bound is exact rather than merely close.
            out["minimum"] = value + 1 if isinstance(value, int) else value
        elif key == "exclusiveMaximum":
            out["maximum"] = value - 1 if isinstance(value, int) else value
        elif key in _ALLOWED_SCHEMA_KEYS:
            out[key] = _sanitize_schema(value)
        # Anything else (additionalProperties, $defs, const, ...) is dropped:
        # the args are re-validated against the real Pydantic model server-side.

    # Gemini rejects a `required` entry with no matching property.
    if "required" in out:
        known = set(out.get("properties", {}))
        out["required"] = [r for r in out["required"] if r in known]
        if not out["required"]:
            del out["required"]
    return out


def _to_contents(messages: list[ChatMessage]) -> list[dict]:
    """Translate the router's provider-neutral history into Gemini `contents`.

    A tool result must go back as a functionResponse part preceded by the
    model's own functionCall. Pasting it in as user text instead leaves no
    trace in the history that the call ever happened, so the model calls the
    same tool again on the next iteration — which is how one "add 3" turned
    into nine items in the cart.
    """
    contents: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            name = m.get("tool_name") or "tool"
            try:
                response = json.loads(m.get("content") or "{}")
            except json.JSONDecodeError:
                response = {"result": m.get("content", "")}
            if not isinstance(response, dict):
                response = {"result": response}
            call_part: dict = {"functionCall": {"name": name, "args": m.get("tool_args") or {}}}
            # Gemini 3 refuses a history whose functionCall parts are missing
            # the signature it issued with them.
            if signature := m.get("tool_signature"):
                call_part["thoughtSignature"] = signature
            contents.append({"role": "model", "parts": [call_part]})
            contents.append({"role": "user", "parts": [
                {"functionResponse": {"name": name, "response": response}},
            ]})
        elif role in ("user", "assistant") and m.get("content"):
            contents.append({
                "role": "model" if role == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            })
    return contents


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or settings.gemini_model

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float
    ) -> ChatResponse:
        if not self.api_key:
            raise ProviderUnavailable("GEMINI_API_KEY not configured")

        system = [m["content"] for m in messages if m.get("role") == "system"]
        contents = _to_contents(messages)
        body: dict = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system[0]}]}
        if tools:
            body["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": _sanitize_schema(t["parameters"])}
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
            ToolCall(
                name=p["functionCall"]["name"],
                args=p["functionCall"].get("args", {}),
                signature=p.get("thoughtSignature"),
            )
            for p in parts if "functionCall" in p
        ]
        if tool_calls:
            return ChatResponse(tool_calls=tool_calls, provider=self.name)

        text = "".join(p.get("text", "") for p in parts)
        return ChatResponse(text=text, provider=self.name)
