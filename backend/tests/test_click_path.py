"""Clicking must be worth exactly as much as asking — no more.

The buyer can now edit the cart and check out by clicking, which skips the
model entirely. That is a second way into the cart, so these prove it is bound
by the same rules the assistant is: same catalog pricing, the same 11 rules at
checkout, and the same refusal once a cart is locked.
"""
import os

os.environ["OFFLINE_MODE"] = "True"

import httpx
import pytest
import pytest_asyncio
from ulid import ULID

from app.db.documents import Product, Session, StockInfo
from app.main import app

NOW = "2026-01-01T00:00:00Z"
HEADERS = {"X-API-Key": "dev-local-key"}


@pytest_asyncio.fixture
async def client(db):
    await Product(
        id="RZ-SHOE-1", title="Trailrunner X", category="footwear", brand="Vaayu",
        price_paise=429900, stock=StockInfo(available=40, reserved=0),
        search_text="trailrunner running shoe", version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t",
    ) as c:
        yield c


async def _session() -> str:
    session_id = str(ULID())
    await Session(id=session_id, channel="human_chat", created_at=NOW).insert()
    return session_id


@pytest.mark.asyncio
async def test_a_clicked_add_is_priced_by_the_catalog(client):
    sid = await _session()

    r = await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 1}, headers=HEADERS)

    assert r.status_code == 200
    assert r.json()["total_paise"] == 429900  # the catalog's price, not the caller's


@pytest.mark.asyncio
async def test_a_clicked_checkout_still_runs_every_rule(client):
    """No model is involved, so the rulebook is the only thing standing
    between this click and a payment. It still runs in full."""
    sid = await _session()
    await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 2}, headers=HEADERS)

    r = await client.post(f"/api/v1/checkout/{sid}", headers=HEADERS)

    body = r.json()
    assert body["status"] == "approval_required"  # ₹8,598 is under the cap, over the threshold
    assert len(body["findings"]) == 11


@pytest.mark.asyncio
async def test_a_clicked_checkout_under_the_threshold_pays(client):
    """The click path reaches a real payment link too, not just refusals."""
    sid = await _session()
    await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 1}, headers=HEADERS)

    r = await client.post(f"/api/v1/checkout/{sid}", headers=HEADERS)

    body = r.json()
    assert body["status"] == "paid_link_created"  # ₹4,299, inside every limit
    assert body["payment_link_url"]


@pytest.mark.asyncio
async def test_clicking_cannot_exceed_the_hard_cap(client):
    sid = await _session()
    await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 10}, headers=HEADERS)

    r = await client.post(f"/api/v1/checkout/{sid}", headers=HEADERS)

    assert r.json()["status"] == "denied"  # ₹42,990 is over the ₹25,000 cap


@pytest.mark.asyncio
async def test_a_locked_cart_refuses_a_clicked_edit(client):
    """Checkout locks the cart against the verdict just issued. A click must
    not be able to change the order out from under that decision."""
    sid = await _session()
    await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 1}, headers=HEADERS)
    await client.post(f"/api/v1/checkout/{sid}", headers=HEADERS)

    r = await client.patch(f"/api/v1/cart/{sid}/items/RZ-SHOE-1", json={"qty": 5}, headers=HEADERS)

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CART_LOCKED"


@pytest.mark.asyncio
async def test_a_clicked_quantity_is_capped(client):
    """The per-line limit is enforced at the edge, not just in the tool schema."""
    sid = await _session()

    r = await client.post(f"/api/v1/cart/{sid}/items", json={"sku": "RZ-SHOE-1", "qty": 99}, headers=HEADERS)

    assert r.status_code == 422
