import json
import time
from datetime import datetime, timezone

from pydantic import ValidationError

from app.agent.llm.base import ChatMessage, LLMUnavailable
from app.agent.llm.router import llm_router
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools.registry import TOOLS, tool_specs_json
from app.audit.service import audit
from app.config import settings
from app.db.documents import Message, Session
from app.errors import RazoError
from app.services.catalog_service import catalog_service

HISTORY_WINDOW = 12


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _load_history(session_id: str) -> list[ChatMessage]:
    """Sorts on (turn, _id) rather than turn alone, via the raw collection —
    Beanie's `.sort()` does not alias `id` to `_id`, so `.sort("-id")` is
    silently a no-op. A single turn can carry several tool messages, and
    without a real tiebreaker their relative order is whatever Mongo happens
    to return, which scrambles which tool result is 'most recent' inside the
    turn loop and can make a later tool call act on stale data."""
    cursor = (
        Message.get_motor_collection()
        .find({"session_id": session_id})
        .sort([("turn", -1), ("_id", -1)])
        .limit(HISTORY_WINDOW)
    )
    docs = await cursor.to_list(length=HISTORY_WINDOW)
    docs.reverse()
    # Only the conversation itself is replayed. A previous turn's tool traffic
    # is deliberately left out: the assistant's reply already summarises what
    # the tools returned, the raw results are stale by now, and a stored tool
    # message has lost the arguments and provider signature that a replayed
    # function call needs to be well-formed. Live tool results are still
    # appended in full inside the turn loop below, where that context exists.
    return [
        {"role": doc["role"], "content": doc["content"]}
        for doc in docs
        if doc["role"] in ("user", "assistant") and doc.get("content")
    ]


async def _persist(session_id: str, turn: int, role: str, content: str, tool_name: str | None = None, tool_args: dict | None = None):
    await Message(
        session_id=session_id, turn=turn, role=role, content=content,
        tool_name=tool_name, tool_args=tool_args, created_at=_now(),
    ).insert()


def _explain_invalid_args(exc: ValidationError) -> str:
    """Turn pydantic's diagnostics into a sentence a shopper can act on."""
    parts = []
    for err in exc.errors():
        field = ".".join(str(loc) for loc in err.get("loc", ())) or "input"
        kind, ctx = err.get("type", ""), err.get("ctx") or {}
        if kind == "less_than_equal":
            parts.append(f"{field} can be at most {ctx.get('le')}")
        elif kind == "greater_than":
            parts.append(f"{field} must be more than {ctx.get('gt')}")
        elif kind == "greater_than_equal":
            parts.append(f"{field} must be at least {ctx.get('ge')}")
        elif kind == "missing":
            parts.append(f"{field} is required")
        else:
            parts.append(f"{field} is not valid")
    return "; ".join(parts) + "."


async def _run_tool_call(session_id: str, name: str, args: dict, trace_id: str) -> dict:
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": "UNKNOWN_TOOL", "message": f"No such tool: {name}"}

    started = time.monotonic()
    try:
        validated = spec.args_model(**args)
        result = await spec.handler(session_id, **validated.model_dump())
        outcome, reason = "ok", f"The assistant called {name} and it succeeded."
    except RazoError as e:
        result = {"error": e.code, "message": e.user_message}
        outcome, reason = "failed", f"The assistant called {name}, which refused: {e.user_message}"
    except ValidationError as e:
        # str(ValidationError) is a developer diagnostic, complete with a
        # pydantic.dev link, and the model repeats whatever it is handed
        # straight back to the shopper. Say what was wrong in plain English.
        result = {"error": "INVALID_ARGS", "message": _explain_invalid_args(e)}
        outcome, reason = "failed", f"The assistant called {name} with arguments the rulebook rejected."
    except Exception as e:  # unexpected failure — never crash the turn
        result = {"error": "TOOL_FAILED", "message": str(e)}
        outcome, reason = "failed", f"The assistant called {name} with arguments it could not accept."

    await audit.record(
        actor="agent", action="tool.invoked", session_id=session_id, trace_id=trace_id,
        subject={"type": "tool", "id": name}, input=args,
        output={"error": result.get("error")} if "error" in result else {"ok": True},
        reason=reason, outcome=outcome, latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result


def _policy_from_tool_result(tool_name: str, result: dict) -> dict | None:
    """Lifts the verdict out of whatever tool produced one, so the UI can
    render it verbatim instead of parsing it back out of the reply text.
    `check_policy` returns a verdict directly; `request_checkout` returns an
    outcome that carries the reason and (on a denial) the findings."""
    if tool_name == "check_policy" and "decision" in result:
        findings = result.get("findings", [])
        return {
            "decision": result["decision"],
            "reason_summary": result.get("reason_summary", ""),
            "findings": findings,
            "violations": [f for f in findings if f.get("outcome") != "pass"],
        }

    if tool_name == "request_checkout" and "status" in result:
        decision = {
            "paid_link_created": "ALLOW",
            "approval_required": "REQUIRE_APPROVAL",
            "denied": "DENY",
        }.get(result["status"])
        if decision is None:
            return None
        findings = result.get("findings", []) or result.get("violations", [])
        return {
            "decision": decision,
            "reason_summary": result.get("reason", ""),
            "findings": findings,
            "violations": [f for f in findings if f.get("outcome") != "pass"],
        }
    return None


def _next_action_from_tool_result(tool_name: str, result: dict) -> dict | None:
    if tool_name != "request_checkout":
        return None
    status = result.get("status")
    if status == "paid_link_created":
        return {
            "type": "payment_link",
            "payment_link_url": result.get("payment_link_url"),
            "order_id": result.get("order_id"),
        }
    if status == "approval_required":
        return {
            "type": "awaiting_approval",
            "approval_id": result.get("approval_id"),
            "expires_at": result.get("expires_at"),
        }
    return None


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
    trace_id = f"{session_id}:{turn}"

    await _persist(session_id, turn, "user", user_text)
    await audit.record(
        actor="buyer", action="message.received", session_id=session_id, trace_id=trace_id,
        input={"turn": turn, "chars": len(user_text)},
        reason=f"The buyer sent a message on turn {turn}.",
    )

    messages: list[ChatMessage] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *await _load_history(session_id),
    ]
    tools = tool_specs_json()
    reply_text: str | None = None
    policy_view: dict | None = None
    next_action: dict | None = None
    suggestions: list[dict] = []
    products: list[dict] = []

    try:
        for _ in range(settings.agent_max_tool_iters):
            remaining = settings.agent_turn_budget_s - (time.monotonic() - start)
            if remaining <= 0:
                reply_text = "That's taking a little longer than expected — could you repeat your last message?"
                await audit.record(
                    actor="agent", action="agent.budget_exhausted", session_id=session_id, trace_id=trace_id,
                    reason=f"The turn hit its {settings.agent_turn_budget_s}s budget, so the assistant "
                           "stopped and answered with what it had rather than running on.",
                    outcome="degraded",
                )
                break

            response = await llm_router.chat(
                messages, tools, timeout_s=min(settings.llm_timeout_s, max(remaining, 1.0)),
                session_id=session_id, trace_id=trace_id,
            )

            if response.tool_calls:
                call = response.tool_calls[0]
                result = await _run_tool_call(session_id, call.name, call.args, trace_id)

                if (verdict := _policy_from_tool_result(call.name, result)) is not None:
                    policy_view = verdict
                if (action := _next_action_from_tool_result(call.name, result)) is not None:
                    next_action = action
                # Surfaced to the UI so a suggestion can be a button rather
                # than something the buyer has to retype. The click still goes
                # back through chat, so the agent and the rulebook stay in the
                # path exactly as if they had typed it.
                if picks := result.get("suggestions"):
                    suggestions = picks
                # The products the assistant just described, handed to the UI
                # as data so it can render an Add button per row instead of
                # asking the buyer to retype a name back at it.
                if call.name == "search_catalog" and result.get("items"):
                    products = result["items"][:5]

                content = json.dumps(result)
                tool_msg: ChatMessage = {
                    "role": "tool", "content": content,
                    "tool_name": call.name, "tool_args": call.args,
                }
                if call.signature:
                    tool_msg["tool_signature"] = call.signature
                messages.append(tool_msg)
                await _persist(session_id, turn, "tool", content, tool_name=call.name, tool_args=call.args)
                continue

            reply_text = response.text or ""
            break
        else:
            reply_text = "I've noted your request — could you tell me a bit more so I can finish it?"
            await audit.record(
                actor="agent", action="agent.budget_exhausted", session_id=session_id, trace_id=trace_id,
                reason=f"The assistant used all {settings.agent_max_tool_iters} of its tool calls without "
                       "reaching an answer, so it stopped and replied honestly.",
                outcome="degraded",
            )
    except LLMUnavailable:
        mode = "degraded"
        reply_text = await _degraded_reply(user_text)
        await audit.record(
            actor="agent", action="llm.degraded", session_id=session_id, trace_id=trace_id,
            reason="Every AI provider in the failover chain was unreachable, so the reply came from a "
                   "direct catalog search instead. The rulebook is unaffected by this.",
            outcome="degraded",
        )

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
        "policy": policy_view,
        "next_action": next_action,
        "suggestions": suggestions,
        "products": products,
        "trace_id": trace_id,
    }
