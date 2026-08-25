from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from app.config import settings
from app.errors import RazoError

_BASE_URL = "https://api.razorpay.com/v1"


class RazorpayClient(Protocol):
    async def create_order(self, amount_paise: int, currency: str, receipt: str, notes: dict) -> dict: ...
    async def create_payment_link(self, amount_paise: int, currency: str, order_id: str, notes: dict) -> dict: ...


class LiveRazorpayClient:
    """Thin httpx wrapper — HTTP Basic auth, a short timeout, and nothing
    else. Called only after a signed ALLOW verdict has been re-verified;
    it never sees model output."""

    async def create_order(self, amount_paise: int, currency: str, receipt: str, notes: dict) -> dict:
        return await self._post("/orders", {
            "amount": amount_paise, "currency": currency, "receipt": receipt, "notes": notes,
        })

    async def create_payment_link(self, amount_paise: int, currency: str, order_id: str, notes: dict) -> dict:
        expire_by = int((datetime.now(timezone.utc) + timedelta(minutes=settings.payment_link_expiry_minutes)).timestamp())
        return await self._post("/payment_links", {
            "amount": amount_paise, "currency": currency,
            "reference_id": order_id, "expire_by": expire_by, "notes": notes,
        })

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=_BASE_URL, auth=(settings.razorpay_key_id, settings.razorpay_key_secret), timeout=10.0,
            ) as client:
                resp = await client.post(path, json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                raise RazoError(
                    "PAYMENT_UPSTREAM", 502, "Payment provider rejected the request.",
                    detail={"status": e.response.status_code}, retryable=False,
                ) from e
            raise RazoError(
                "PAYMENT_UPSTREAM", 502, "Payment provider is slow; your cart is saved.",
                detail={"status": e.response.status_code}, retryable=True,
            ) from e
        except httpx.HTTPError as e:
            raise RazoError(
                "PAYMENT_UPSTREAM", 502, "Payment provider is slow; your cart is saved.", retryable=True,
            ) from e


class FakeRazorpayClient:
    """Deterministic fixtures — no network. Used in OFFLINE_MODE and whenever
    no Razorpay keys are configured, so a keyless clone can still run the
    full checkout flow."""

    async def create_order(self, amount_paise: int, currency: str, receipt: str, notes: dict) -> dict:
        return {"id": f"order_fake_{receipt}", "amount": amount_paise, "currency": currency, "status": "created"}

    async def create_payment_link(self, amount_paise: int, currency: str, order_id: str, notes: dict) -> dict:
        link_id = f"plink_fake_{order_id}"
        return {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created"}


def get_razorpay_client() -> RazorpayClient:
    if settings.offline_mode or not (settings.razorpay_key_id and settings.razorpay_key_secret):
        return FakeRazorpayClient()
    return LiveRazorpayClient()
