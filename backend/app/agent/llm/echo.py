import json
import re

from app.agent.llm.base import ChatMessage, ChatResponse, ToolCall, ToolSpecDict

_ADD_WORDS = re.compile(r"\b(add|buy|get|take|want|order)\b", re.I)
_CHECKOUT_WORDS = re.compile(r"\b(checkout|check out|pay|purchase|confirm|place the order)\b", re.I)
_QTY = re.compile(r"\b(\d+)\b")


class EchoProvider:
    """Deterministic, keyword-based stand-in for an LLM. No network, no API
    key, always available — the last link in the failover chain, and what
    lets the whole system run with zero configured providers."""

    name = "echo"

    async def chat(
        self, messages: list[ChatMessage], tools: list[ToolSpecDict], timeout_s: float
    ) -> ChatResponse:
        last_user_idx = self._last_index(messages, "user")
        last_user = messages[last_user_idx]["content"] if last_user_idx is not None else ""

        # Only a tool result that arrived *after* the latest user message
        # belongs to this turn's in-progress loop. A tool result from before
        # it is a completed prior turn's leftover — using it here would
        # silently act on stale state (e.g. re-adding whatever the previous
        # add_to_cart call did instead of starting a fresh request).
        last_tool_msg = None
        if last_user_idx is not None:
            for m in messages[last_user_idx + 1:]:
                if m.get("role") == "tool":
                    last_tool_msg = m

        result: dict = {}
        kind = "none"
        if last_tool_msg is not None:
            try:
                result = json.loads(last_tool_msg["content"])
            except (json.JSONDecodeError, KeyError):
                result = {}
            kind = self._classify(last_tool_msg.get("tool_name"), result)

        if kind == "checkout":
            return ChatResponse(text=self._checkout_summary(result), provider=self.name)

        # Checked before anything else so it still fires when the history
        # window carries a tool result from an earlier turn.
        if last_user and _CHECKOUT_WORDS.search(last_user):
            return ChatResponse(
                tool_calls=[ToolCall(name="request_checkout", args={"confirm": True})], provider=self.name,
            )

        if kind == "none":
            return ChatResponse(
                tool_calls=[ToolCall(name="search_catalog", args={"query": last_user.strip(), "limit": 5})],
                provider=self.name,
            )

        if kind == "search_results" and last_user and _ADD_WORDS.search(last_user):
            items = result.get("items", [])
            if items:
                qty_match = _QTY.search(last_user)
                qty = int(qty_match.group(1)) if qty_match else 1
                return ChatResponse(
                    tool_calls=[ToolCall(name="add_to_cart", args={"sku": items[0]["sku"], "qty": qty})],
                    provider=self.name,
                )

        if kind == "cart":
            return ChatResponse(text=self._cart_summary(result), provider=self.name)
        if kind == "search_results":
            items = result.get("items", [])
            if items:
                lines = [f"- {i['title']} — {i['price_display']}" for i in items[:3]]
                text = "Here's what I found:\n" + "\n".join(lines)
            else:
                text = "I couldn't find anything matching that. Try different words?"
            return ChatResponse(text=text, provider=self.name)
        if kind == "product":
            return ChatResponse(text=f"{result['title']} — {result['price_display']}", provider=self.name)
        if kind == "error":
            return ChatResponse(text=result.get("message", "That didn't work."), provider=self.name)

        return ChatResponse(text="Got it.", provider=self.name)

    @staticmethod
    def _classify(tool_name: str | None, result: dict) -> str:
        # Errors are checked first: a failed add_to_cart still carries the
        # add_to_cart tool name, and reporting it as an empty cart would tell
        # the buyer something untrue.
        if "error" in result:
            return "error"
        if tool_name == "request_checkout" or "status" in result:
            return "checkout"
        if tool_name in ("add_to_cart", "update_cart_item", "get_cart") or "subtotal_paise" in result:
            return "cart"
        if tool_name == "search_catalog" or "total" in result and "items" in result:
            return "search_results"
        if tool_name == "get_product" or ("sku" in result and "price_display" in result):
            return "product"
        return "unknown"

    @staticmethod
    def _last_index(messages: list[ChatMessage], role: str) -> int | None:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == role:
                return i
        return None

    @staticmethod
    def _checkout_summary(result: dict) -> str:
        """Reports the rulebook's verdict verbatim — the stand-in must be as
        honest about a refusal as the LLM path is."""
        reason = result.get("reason", "")
        status = result.get("status")
        if status == "paid_link_created":
            return f"All clear — {reason}\nPay here: {result.get('payment_link_url')}"
        if status == "approval_required":
            return f"{reason}\nI've sent this to the merchant for approval — I'll let you know shortly."
        if status == "denied":
            return f"I can't put that order through. {reason}"
        return result.get("message", "That didn't work.")

    @staticmethod
    def _cart_summary(cart: dict) -> str:
        items = cart.get("items", [])
        if not items:
            return "Your cart is empty."
        lines = [f"- {i['qty']} × {i['sku']} @ ₹{i['unit_price_paise']/100:,.2f}" for i in items]
        total = cart.get("total_paise", 0) / 100
        return "Cart:\n" + "\n".join(lines) + f"\nTotal: ₹{total:,.2f}"
