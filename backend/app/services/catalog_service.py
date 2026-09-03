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


# Shoppers do not speak in catalog categories. The seed tags a running shoe
# "footwear" and never once says "shoe", so a literal search for what people
# actually type finds nothing. This maps their word onto ours.
_CATEGORY_SYNONYMS: dict[str, str] = {
    "shoe": "footwear", "shoes": "footwear", "sneaker": "footwear", "sneakers": "footwear",
    "trainer": "footwear", "trainers": "footwear", "boot": "footwear", "boots": "footwear",
    "sandal": "footwear", "sandals": "footwear", "footwear": "footwear",
    "earbud": "electronics", "earbuds": "electronics", "buds": "electronics",
    "airbuds": "electronics", "airpods": "electronics", "earphone": "electronics",
    "earphones": "electronics", "headphone": "electronics", "headphones": "electronics",
    "gadget": "electronics", "gadgets": "electronics", "electronics": "electronics",
    "clothes": "apparel", "clothing": "apparel", "shirt": "apparel", "tshirt": "apparel",
    "jacket": "apparel", "hoodie": "apparel", "apparel": "apparel", "wear": "apparel",
    "book": "books", "books": "books", "reading": "books",
    "makeup": "beauty", "skincare": "beauty", "cosmetics": "beauty", "beauty": "beauty",
    "food": "grocery", "snacks": "grocery", "coffee": "grocery", "grocery": "grocery",
    "kitchen": "home", "furniture": "home", "decor": "home", "home": "home",
    "fitness": "sports", "gym": "sports", "workout": "sports", "sports": "sports",
}


def _infer_category(q: str | None) -> str | None:
    """The category a shopper's own words point at, if any."""
    if not q:
        return None
    for token in _tokens(q):
        if (category := _CATEGORY_SYNONYMS.get(token)) is not None:
            return category
    return None


# Which categories sit naturally in the same basket. Static and boring on
# purpose: a growth suggestion still has to be a real product at a real
# catalog price, so the model gets candidates to choose between, never a
# free hand to invent an offer.
_COMPLEMENTS: dict[str, tuple[str, ...]] = {
    "footwear": ("sports", "apparel"),
    "apparel": ("footwear", "beauty"),
    "sports": ("footwear", "apparel"),
    "electronics": ("home", "books"),
    "home": ("electronics", "grocery"),
    "grocery": ("home", "beauty"),
    "beauty": ("apparel", "grocery"),
    "books": ("home", "electronics"),
}

# An upgrade should be a nudge, not a different budget conversation: the next
# steps up, never a jump from a ₹605 shoe to a ₹12,648 one.
_MAX_UPGRADE_MULTIPLE = 3.0

# What a sensible add-on costs relative to the main item. Ranking by nearness
# to this keeps the attach proportionate — a ₹500 accessory next to a ₹12,000
# pair of headphones is as wrong as a ₹12,000 one next to a ₹600 book.
_PAIR_PRICE_RATIO = 0.5


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
        # "shoes under 3000" carries a category the caller did not pass.
        category = category or _infer_category(q)
        try:
            # Three passes, each looser than the last, stopping at the first
            # that finds anything. A shopper who says "shoe under 3000" or
            # "air buds" should not be told the shop is empty.
            products = await self._query(q, category, price_max_paise)
            if not products:
                # $text matches whole words only, so "buds" never reaches
                # "Ecobuds". Substring is what offline mode has always used.
                products = await self._query(q, category, price_max_paise, substring=True)
            if not products and category:
                # Their words named a department but nothing else we stock —
                # show the department rather than nothing.
                products = await self._query(None, category, price_max_paise)
            views = [_to_view(p) for p in products]
        except PyMongoError:
            log.warning("Catalog search fell back to the boot snapshot — Mongo unreachable.")
            await self._audit_db_unavailable("search")
            views = self._search_snapshot(q, category, price_max_paise)

        if in_stock_only:
            views = [v for v in views if v.in_stock]

        views = _rank(views, q)[:limit]
        return SearchPage(items=views, total=len(views), limit=limit)

    async def categories(self) -> list[dict]:
        """What the shop sells, as departments. An AI buyer with a mandate
        scoped to certain categories needs to know ours before it shops."""
        try:
            products = await Product.find(Product.active == True).to_list()  # noqa: E712
            views = [_to_view(p) for p in products]
        except PyMongoError:
            await self._audit_db_unavailable("categories")
            views = list(self._snapshot)

        by_category: dict[str, list[ProductView]] = {}
        for view in views:
            by_category.setdefault(view.category, []).append(view)

        return [
            {
                "category": name,
                "product_count": len(rows),
                "min_price_paise": min(r.price_paise for r in rows),
                "max_price_paise": max(r.price_paise for r in rows),
                "in_stock_count": sum(1 for r in rows if r.in_stock),
            }
            for name, rows in sorted(by_category.items())
        ]

    async def resolve(self, query: str, limit: int = 3) -> dict:
        """Natural language in, concrete SKUs out.

        An AI buyer is handed an instruction like "buy running shoes", not a
        SKU. This is the one hop it cannot make on its own, and doing it here
        means the resolution is the catalog's answer rather than a guess made
        by whatever model happens to be driving.
        """
        page = await self.search(q=query, limit=limit)
        return {
            "query": query,
            "matches": [
                {
                    "sku": v.sku, "title": v.title, "brand": v.brand, "category": v.category,
                    "price_paise": v.price_paise, "price_display": v.price_display,
                    "in_stock": v.in_stock,
                }
                for v in page.items
            ],
            "resolved": bool(page.items),
        }

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
    async def _query(
        q: str | None, category: str | None, price_max_paise: int | None, substring: bool = False,
    ) -> list[Product]:
        query: dict = {"active": True}
        if category:
            query["category"] = category
        if price_max_paise is not None:
            query["price_paise"] = {"$lte": price_max_paise}

        if q:
            if substring or settings.offline_mode:
                tokens = [t for t in _tokens(q) if len(t) >= 3]
                if tokens:
                    query["$or"] = [
                        {"search_text": {"$regex": re.escape(t), "$options": "i"}} for t in tokens
                    ]
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

    async def recommend(
        self, anchor_sku: str, exclude_skus: frozenset[str] = frozenset(), limit: int = 2,
    ) -> dict:
        """Upgrades and pairings for one anchor product, drawn from the catalog.

        Returns candidates only. Nothing here reserves stock, moves money or
        bypasses a rule — an accepted suggestion goes through add_to_cart and
        the rulebook exactly like anything else the buyer picked themselves.
        """
        anchor = await self.get(anchor_sku)
        skip = set(exclude_skus) | {anchor.sku}

        try:
            products = await Product.find({"active": True}).to_list()
            pool = [_to_view(p) for p in products]
        except PyMongoError:
            await self._audit_db_unavailable("recommend")
            pool = list(self._snapshot)

        pool = [v for v in pool if v.in_stock and v.sku not in skip]

        # Nearest steps up in the same category, so the suggestion reads as
        # "the better one" rather than a different budget entirely.
        ceiling = int(anchor.price_paise * _MAX_UPGRADE_MULTIPLE)
        upgrades = sorted(
            (v for v in pool
             if v.category == anchor.category and anchor.price_paise < v.price_paise <= ceiling),
            key=lambda v: v.price_paise,
        )[:limit]

        complements = [v for v in pool if v.category in _COMPLEMENTS.get(anchor.category, ())]
        # An add-on that costs more than the main item reads as a bait and
        # switch, so prefer those at or below it, proportionate to the anchor.
        affordable = [v for v in complements if v.price_paise <= anchor.price_paise]
        target = anchor.price_paise * _PAIR_PRICE_RATIO
        pairs = sorted(
            affordable or complements,
            key=lambda v: (abs(v.price_paise - target), v.price_paise),
        )[:limit]

        return {
            "anchor": {"sku": anchor.sku, "title": anchor.title,
                       "price_paise": anchor.price_paise, "price_display": anchor.price_display,
                       "category": anchor.category},
            "upgrades": [
                {**v.model_dump(),
                 "why": f"A step up from {anchor.title} in the same category, "
                        f"{inr(v.price_paise - anchor.price_paise)} more."}
                for v in upgrades
            ],
            "pairs_with": [
                {**v.model_dump(), "why": f"Commonly bought alongside {anchor.category}."}
                for v in pairs
            ],
        }

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
