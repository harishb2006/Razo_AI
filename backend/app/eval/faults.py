"""Fault injection for designated eval runs. Each fault reproduces one entry
in the failure taxonomy against the real pipeline, so the report's resilience
numbers come from faults that actually fired rather than from a claim."""
from app.agent.llm.base import ChatResponse, ProviderTimeout, RateLimited
from app.errors import RazoError


class RateLimitingProvider:
    """F1 — the designated demo failure: the primary provider 429s us."""

    name = "gemini"

    def __init__(self, failures: int = 99):
        self._remaining = failures
        self.calls = 0

    async def chat(self, messages, tools, timeout_s):
        self.calls += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise RateLimited(retry_after=0)
        return ChatResponse(text="recovered", provider=self.name)


class TimingOutProvider:
    """F2 — the provider accepts the call and never answers."""

    name = "gemini"

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools, timeout_s):
        self.calls += 1
        raise ProviderTimeout("timed out")


class FailingRazorpayClient:
    """F6 — Razorpay 5xx. The cart must survive and the buyer must be told."""

    def __init__(self):
        self.calls = 0

    async def create_order(self, amount_paise, currency, receipt, notes):
        self.calls += 1
        raise RazoError(
            "PAYMENT_UPSTREAM", 502, "Payment provider is slow; your cart is saved.", retryable=True,
        )

    async def create_payment_link(self, amount_paise, currency, order_id, notes):
        raise AssertionError("never reached — the order call fails first")


async def drain_stock(sku: str) -> None:
    """F4 / stock race — the last unit goes between adding to the cart and
    checking out."""
    from app.db.documents import Product

    product = await Product.get(sku)
    if product is not None:
        product.stock.available = 0
        await product.save()


FAULTS = {
    "llm_rate_limit": "The primary LLM provider returns 429 for every call.",
    "llm_timeout": "The primary LLM provider never answers.",
    "razorpay_5xx": "Razorpay returns a server error when creating the order.",
    "stock_race": "The item sells out between add_to_cart and checkout.",
}
