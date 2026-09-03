"""The manifest is a promise to software that cannot ask us what we meant.

`/.well-known/agent-catalog.json` is the whole basis of being "transactable by
an AI buyer": a stranger's program reads it and follows it literally. An entry
that 404s is worse than an absent one, because the buyer has no way to tell a
broken shop from an empty one. This walks every endpoint it advertises.
"""
import os

os.environ["OFFLINE_MODE"] = "True"

import httpx
import pytest
import pytest_asyncio

from app.db.documents import Product, StockInfo
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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.mark.asyncio
async def test_every_endpoint_the_manifest_advertises_exists(client):
    manifest = (await client.get("/.well-known/agent-catalog.json")).json()

    missing = []
    for name, entry in manifest["endpoints"].items():
        method, path = entry.split(" ", 1)
        url = "/api/v1" + path.replace("{sku}", "RZ-SHOE-1").replace("{session_id}", "unknown")
        response = await (
            client.get(url, headers=HEADERS)
            if method == "GET"
            else client.post(url, json={"query": "shoe"}, headers=HEADERS)
        )
        # 404 on a real session id would be a routing failure; the checkout
        # entry is probed with a deliberately unknown session, whose own 404
        # is the documented answer rather than a missing route.
        if response.status_code == 404 and "{session_id}" not in entry:
            missing.append(f"{name} -> {method} {url}")

    assert not missing, f"manifest advertises endpoints that do not exist: {missing}"


@pytest.mark.asyncio
async def test_resolve_turns_words_into_skus(client):
    """An AI buyer is given an instruction, not a SKU."""
    r = await client.post("/api/v1/catalog/resolve", json={"query": "running shoes"}, headers=HEADERS)

    body = r.json()
    assert body["resolved"] is True
    assert body["matches"][0]["sku"] == "RZ-SHOE-1"


@pytest.mark.asyncio
async def test_categories_report_what_is_actually_stocked(client):
    r = await client.get("/api/v1/catalog/categories", headers=HEADERS)

    footwear = next(c for c in r.json() if c["category"] == "footwear")
    assert footwear["product_count"] == 1
    assert footwear["in_stock_count"] == 1
