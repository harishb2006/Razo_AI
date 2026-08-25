"""The echo provider is the last link in the failover chain, so the checkout
path has to work with no LLM at all — including on a later turn, where the
history window still carries a tool result from an earlier turn."""
import json

import pytest

from app.agent.llm.echo import EchoProvider

provider = EchoProvider()


def user(text: str) -> dict:
    return {"role": "user", "content": text}


def tool(name: str, payload: dict) -> dict:
    return {"role": "tool", "content": json.dumps(payload), "tool_name": name}


@pytest.mark.asyncio
async def test_checkout_intent_calls_request_checkout():
    response = await provider.chat([user("checkout please")], [], 1.0)
    assert [c.name for c in response.tool_calls] == ["request_checkout"]


@pytest.mark.asyncio
async def test_checkout_intent_wins_over_a_stale_tool_result_from_an_earlier_turn():
    messages = [
        user("show me shoes"),
        tool("search_catalog", {"items": [{"sku": "RZ-1", "title": "X", "price_display": "₹1"}], "total": 1}),
        user("pay now"),
    ]
    response = await provider.chat(messages, [], 1.0)
    assert [c.name for c in response.tool_calls] == ["request_checkout"]


@pytest.mark.asyncio
async def test_a_denial_is_reported_verbatim_not_glossed_over():
    messages = [
        user("checkout"),
        tool("request_checkout", {"status": "denied", "reason": "Order total exceeds the cap."}),
    ]
    response = await provider.chat(messages, [], 1.0)
    assert not response.tool_calls
    assert "Order total exceeds the cap." in response.text


@pytest.mark.asyncio
async def test_an_escalation_says_it_went_to_the_merchant():
    messages = [
        user("checkout"),
        tool("request_checkout", {"status": "approval_required", "reason": "Above the threshold."}),
    ]
    response = await provider.chat(messages, [], 1.0)
    assert "merchant" in response.text.lower()


@pytest.mark.asyncio
async def test_a_payment_link_is_handed_to_the_buyer():
    messages = [
        user("checkout"),
        tool("request_checkout", {
            "status": "paid_link_created", "reason": "Within all limits.",
            "payment_link_url": "https://rzp.io/i/abc",
        }),
    ]
    response = await provider.chat(messages, [], 1.0)
    assert "https://rzp.io/i/abc" in response.text


@pytest.mark.asyncio
async def test_a_failed_add_reports_the_error_not_an_empty_cart():
    messages = [
        user("add socks"),
        tool("add_to_cart", {"error": "CART_LOCKED", "message": "This cart is being checked out."}),
    ]
    response = await provider.chat(messages, [], 1.0)
    assert response.text == "This cart is being checked out."


@pytest.mark.asyncio
async def test_a_plain_search_still_searches():
    response = await provider.chat([user("running shoes")], [], 1.0)
    assert [c.name for c in response.tool_calls] == ["search_catalog"]
