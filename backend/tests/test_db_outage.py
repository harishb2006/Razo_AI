"""F11 — MongoDB unreachable. Browsing degrades to the boot snapshot;
checkout does not degrade at all, because a price served from stale memory
is exactly the thing the rulebook exists to prevent."""
import pytest
import pytest_asyncio
from pymongo.errors import ServerSelectionTimeoutError

from app.db.documents import AuditEvent, Product, StockInfo
from app.errors import RazoError
from app.services.catalog_service import CatalogService

NOW = "2026-01-01T00:00:00Z"


@pytest_asyncio.fixture
async def catalog(db):
    await Product(
        id="RZ-SHOE-1", title="Trailrunner X", description="road running shoe",
        category="footwear", brand="Vaayu", price_paise=429900,
        stock=StockInfo(available=40, reserved=0), search_text="trailrunner running",
        version=1, updated_at=NOW, created_at=NOW,
    ).insert()
    await Product(
        id="RZ-SOCK-1", title="Merino Socks", description="wool socks",
        category="apparel", brand="Vaayu", price_paise=49900,
        stock=StockInfo(available=100, reserved=0), search_text="merino socks",
        version=1, updated_at=NOW, created_at=NOW,
    ).insert()

    service = CatalogService()
    await service.load_snapshot()
    yield service


def break_mongo(monkeypatch, service: CatalogService):
    async def unreachable(*args, **kwargs):
        raise ServerSelectionTimeoutError("no reachable servers")

    monkeypatch.setattr(service, "_query", unreachable)
    monkeypatch.setattr(Product, "get", unreachable)


@pytest.mark.asyncio
async def test_the_snapshot_is_loaded_at_boot(catalog):
    assert catalog.snapshot_size == 2


@pytest.mark.asyncio
async def test_browsing_survives_a_database_outage(catalog, monkeypatch):
    break_mongo(monkeypatch, catalog)

    page = await catalog.search(q="running")

    assert page.items
    assert page.items[0].sku == "RZ-SHOE-1"


@pytest.mark.asyncio
async def test_snapshot_search_still_honours_filters(catalog, monkeypatch):
    break_mongo(monkeypatch, catalog)

    page = await catalog.search(category="apparel", price_max_paise=100000)

    assert [i.sku for i in page.items] == ["RZ-SOCK-1"]


@pytest.mark.asyncio
async def test_a_single_product_is_still_readable_from_the_snapshot(catalog, monkeypatch):
    break_mongo(monkeypatch, catalog)

    view = await catalog.get("RZ-SHOE-1")

    assert view.price_paise == 429900


@pytest.mark.asyncio
async def test_an_unknown_sku_during_an_outage_is_an_honest_404(catalog, monkeypatch):
    break_mongo(monkeypatch, catalog)

    with pytest.raises(RazoError) as exc:
        await catalog.get("RZ-NOT-REAL")
    assert exc.value.code == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_the_outage_is_recorded_with_its_consequence(catalog, monkeypatch):
    break_mongo(monkeypatch, catalog)

    await catalog.search(q="running")

    event = await AuditEvent.find_one(AuditEvent.action == "db.unavailable")
    assert event is not None
    assert event.outcome == "degraded"
    assert "checkout does not" in event.reason


@pytest.mark.asyncio
async def test_checkout_refuses_rather_than_pricing_from_stale_memory(catalog, monkeypatch):
    """The important half of F11: degrading browsing is fine, degrading the
    money path is not. The buyer gets a clear 503 with a next step, never a
    payment link priced from stale memory and never a raw 500."""
    from fastapi.testclient import TestClient

    from app.db.documents import Session
    from app.main import app

    async def unreachable(*args, **kwargs):
        raise ServerSelectionTimeoutError("no reachable servers")

    monkeypatch.setattr(Session, "get", unreachable)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/checkout/any-session")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_UNAVAILABLE"
    assert "browsing still works" in response.json()["error"]["message"]
