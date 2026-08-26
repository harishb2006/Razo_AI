import logging
import re

from pymongo.errors import PyMongoError

# Excluded from ranking tokens: common enough that matching them says
# nothing about which product was meant. Without this, "add the Baselayer
# Pro" ranks *any* "... Pro" product as a hit on equal footing with the one
# actually named, and whichever happens to sort first in the collection wins.
_STOPWORDS = frozenset({
    "add", "the", "a", "an", "get", "buy", "want", "order", "please", "show",
    "me", "for", "from", "that", "this", "with", "and", "some", "something",
    "need", "one", "two", "three", "of", "to", "in", "is", "it", "at",
})

from app.api.v1.schemas.catalog import ProductView, SearchPage
from app.config import settings
from app.db.documents import Product
from app.domain.money import inr
from app.errors import RazoError, product_not_found

log = logging.getLogger(__name__)


def _tokens(text: str) -> list[str]:
    """Single-character tokens are kept for ranking (not just len>=3): the
    seed catalog names variants 'Trailrunner X' vs '... Pro', and dropping
    'x' as too short throws away the one token that disambiguates them,
    leaving ranking to break the tie on price instead of relevance."""
    return [t for t in re.split(r"\W+", text.lower()) if t and t not in _STOPWORDS]


def _score(view: "ProductView", tokens: list[str]) -> tuple:
    """Deterministic re-rank in Python (LLD §5.1) rather than trusting
    Mongo's own text-match order — reproducible across runs and independent
    of whichever candidate the query engine happened to return first."""
    title = view.title.lower()
    haystack = f"{view.title} {view.brand} {view.category} {view.description}".lower()

    exact_title_hits = sum(1 for t in tokens if t in title)
    brand_hit = 1 if any(t == view.brand.lower() for t in tokens) else 0
    category_hit = 1 if any(t == view.category.lower() for t in tokens) else 0
    other_hits = sum(1 for t in tokens if t not in title and t in haystack)

    score = 3 * exact_title_hits + 2 * brand_hit + 1.5 * category_hit + other_hits
    return (-score, 0 if view.in_stock else 1, view.price_paise)


def _rank(views: list["ProductView"], q: str | None) -> list["ProductView"]:
    tokens = _tokens(q) if q else []
    if not tokens:
        return views
    return sorted(views, key=lambda v: _score(v, tokens))


def _to_view(p: Product) -> ProductView:
    return ProductView(
        sku=p.id,
        title=p.title,
        description=p.description,
        category=p.category,
        brand=p.brand,
        price_paise=p.price_paise,
        price_display=inr(p.price_paise),
        currency=p.currency,
        in_stock=(p.stock.available - p.stock.reserved) > 0,
        qty_available=p.stock.available - p.stock.reserved,
        attributes=p.attributes,
        version=p.version,
        updated_at=p.updated_at,
    )


def db_unavailable() -> RazoError:
    return RazoError(
        "DB_UNAVAILABLE", 503, "Can't take orders this moment — browsing still works.", retryable=True,
    )


class CatalogService:
    """Browsing survives a database outage (F11) via a snapshot taken at
    boot. Deliberately read-only: an outage degrades browsing to stale data,
    it never lets an order through on stale prices — the rulebook re-reads
    the catalog of record, and if that read fails the checkout fails."""

    def __init__(self):
        self._snapshot: list[ProductView] = []

    async def load_snapshot(self) -> int:
        try:
            products = await Product.find(Product.active == True).to_list()  # noqa: E712 — Beanie needs ==
            self._snapshot = [_to_view(p) for p in products]
        except PyMongoError:
            log.warning("Could not load the catalog snapshot at boot; browsing has no fallback.")
            self._snapshot = []
        return len(self._snapshot)

    @property
    def snapshot_size(self) -> int:
        return len(self._snapshot)

    async def search(
        self,
        q: str | None = None,
        category: str | None = None,
        price_max_paise: int | None = None,
        in_stock_only: bool = False,
        limit: int = 10,
    ) -> SearchPage:
        limit = min(limit, settings.catalog_search_limit)
        try:
            products = await self._query(q, category, price_max_paise)
            views = [_to_view(p) for p in products]
        except PyMongoError:
            log.warning("Catalog search fell back to the boot snapshot — Mongo unreachable.")
            await self._audit_db_unavailable("search")
            views = self._search_snapshot(q, category, price_max_paise)

        if in_stock_only:
            views = [v for v in views if v.in_stock]

        views = _rank(views, q)[:limit]
        return SearchPage(items=views, total=len(views), limit=limit)

    async def get(self, sku: str) -> ProductView:
        try:
            product = await Product.get(sku)
        except PyMongoError:
            await self._audit_db_unavailable("get")
            match = next((v for v in self._snapshot if v.sku == sku), None)
            if match is None:
                raise product_not_found(sku) from None
            return match

        if product is None or not product.active:
            raise product_not_found(sku)
        return _to_view(product)

    @staticmethod
    async def _query(q: str | None, category: str | None, price_max_paise: int | None) -> list[Product]:
        query: dict = {"active": True}
        if category:
            query["category"] = category
        if price_max_paise is not None:
            query["price_paise"] = {"$lte": price_max_paise}

        if q:
            if settings.offline_mode:
                tokens = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 3]
                if tokens:
                    query["$or"] = [{"search_text": {"$regex": t, "$options": "i"}} for t in tokens]
            else:
                query["$text"] = {"$search": q}

        return await Product.find(query).limit(50).to_list()

    def _search_snapshot(
        self, q: str | None, category: str | None, price_max_paise: int | None,
    ) -> list[ProductView]:
        results = self._snapshot
        if category:
            results = [v for v in results if v.category == category]
        if price_max_paise is not None:
            results = [v for v in results if v.price_paise <= price_max_paise]
        if q:
            tokens = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 3]
            if tokens:
                results = [
                    v for v in results
                    if any(t in f"{v.title} {v.brand} {v.category} {v.description}".lower() for t in tokens)
                ]
        return results

    @staticmethod
    async def _audit_db_unavailable(operation: str) -> None:
        from app.audit.service import audit_safe

        await audit_safe(
            actor="system", action="db.unavailable",
            input={"operation": operation},
            reason="MongoDB was unreachable, so the catalog was served from the snapshot taken at boot. "
                   "Browsing continues; checkout does not, because prices must come from the live catalog.",
            outcome="degraded",
        )

    def manifest(self) -> dict:
        from app.policy.policy import policy

        return {
            "name": "Razo_AI Merchant Catalog",
            "version": "v1",
            "base_url": "/api/v1",
            "auth": {"type": "api_key", "header": "X-API-Key"},
            "endpoints": {
                "search": "GET /catalog/products",
                "get_product": "GET /catalog/products/{sku}",
                "categories": "GET /catalog/categories",
                "schema": "GET /catalog/schema",
                "resolve": "POST /catalog/resolve",
                "checkout": "POST /checkout/{session_id}",
            },
            "product_schema": ProductView.model_json_schema(),
            "policy_limits": {
                "max_order_paise": policy.limits.max_order_paise,
                "buyer_agent_max_order_paise": policy.buyer_agent.max_order_paise,
                "denied_categories": sorted(policy.deny_categories),
                "max_qty_per_line": policy.limits.max_qty_per_line,
                "currency": sorted(policy.allowed_currencies),
                "mandate_required_for_agents": policy.buyer_agent.require_mandate,
            },
        }


catalog_service = CatalogService()
