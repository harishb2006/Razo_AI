import json
import time
from datetime import datetime, timezone

from app.agent.llm.base import ChatMessage, LLMUnavailable
from app.agent.llm.router import llm_router
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools.registry import TOOLS, tool_specs_json
from app.config import settings
from app.db.documents import Message, Session
from app.errors import RazoError
from app.services.catalog_service import catalog_service

HISTORY_WINDOW = 12


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _load_history(session_id: str) -> list[ChatMessage]:
    msgs = (
        await Message.find(Message.session_id == session_id)
        .sort("-turn")
        .limit(HISTORY_WINDOW)
        .to_list()
    )
    msgs.reverse()
    history: list[ChatMessage] = []
    for m in msgs:
        entry: ChatMessage = {"role": m.role, "content": m.content}
        if m.tool_name:
            entry["tool_name"] = m.tool_name
        history.append(entry)
    return history


async def _persist(session_id: str, turn: int, role: str, content: str, tool_name: str | None = None, tool_args: dict | None = None):
    await Message(
        session_id=session_id, turn=turn, role=role, content=content,
        tool_name=tool_name, tool_args=tool_args, created_at=_now(),
    ).insert()


async def _run_tool_call(session_id: str, name: str, args: dict) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": "UNKNOWN_TOOL", "message": f"No such tool: {name}"}
    try:
        validated = spec.args_model(**args)
        return await spec.handler(session_id, **validated.model_dump())
    except RazoError as e:
        return {"error": e.code, "message": e.user_message}
    except Exception as e:  # malformed args, unexpected failure — never crash the turn
        return {"error": "TOOL_FAILED", "message": str(e)}


async def _degraded_reply(user_text: str) -> str:
    page = await catalog_service.search(q=user_text, limit=5)
    if page.items:
        lines = [f"- {i.title} — {i.price_display}" for i in page.items[:3]]
        return "Working in direct-search mode right now — here's what I found:\n" + "\n".join(lines)
    return "Working in direct-search mode right now — I couldn't find a match for that."


async def handle_turn(session_id: str, user_text: str) -> dict:
    """Bounded tool-calling loop: <=AGENT_MAX_TOOL_ITERS iterations,
    <=AGENT_TURN_BUDGET_S wall-clock. Every provider failure degrades to a
    non-LLM catalog search rather than surfacing an error to the buyer."""
    session = await Session.get(session_id)
    if session is None:
        raise RazoError("SESSION_NOT_FOUND", 404, "I couldn't find that session.")

    turn = session.turn_count + 1
    start = time.monotonic()
    mode = "normal"

    await _persist(session_id, turn, "user", user_text)

    messages: list[ChatMessage] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *await _load_history(session_id),
    ]
    tools = tool_specs_json()
    reply_text: str | None = None

    try:
        for _ in range(settings.agent_max_tool_iters):
            remaining = settings.agent_turn_budget_s - (time.monotonic() - start)
            if remaining <= 0:
                reply_text = "That's taking a little longer than expected — could you repeat your last message?"
                break

            response = await llm_router.chat(
                messages, tools, timeout_s=min(settings.llm_timeout_s, max(remaining, 1.0))
            )

            if response.tool_calls:
                call = response.tool_calls[0]
                result = await _run_tool_call(session_id, call.name, call.args)
                content = json.dumps(result)
                messages.append({"role": "tool", "content": content, "tool_name": call.name})
                await _persist(session_id, turn, "tool", content, tool_name=call.name, tool_args=call.args)
                continue

            reply_text = response.text or ""
            break
        else:
            reply_text = "I've noted your request — could you tell me a bit more so I can finish it?"
    except LLMUnavailable:
        mode = "degraded"
        reply_text = await _degraded_reply(user_text)

    await _persist(session_id, turn, "assistant", reply_text or "")

    await Session.get_motor_collection().update_one({"_id": session_id}, {"$set": {"turn_count": turn}})
    session = await Session.get(session_id)
    latency_ms = int((time.monotonic() - start) * 1000)

    return {
        "session_id": session_id,
        "turn": turn,
        "mode": mode,
        "reply": reply_text,
        "cart": session.cart.model_dump(),
        "latency_ms": latency_ms,
    }
