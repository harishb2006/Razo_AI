import re

from app.api.v1.schemas.catalog import ProductView, SearchPage
from app.config import settings
from app.db.documents import Product
from app.domain.money import inr
from app.errors import product_not_found


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


class CatalogService:
    async def search(
        self,
        q: str | None = None,
        category: str | None = None,
        price_max_paise: int | None = None,
        in_stock_only: bool = False,
        limit: int = 10,
    ) -> SearchPage:
        limit = min(limit, settings.catalog_search_limit)
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
                cursor = Product.find(query)
            else:
                query["$text"] = {"$search": q}
                cursor = Product.find(query)
        else:
            cursor = Product.find(query)

        products = await cursor.limit(50).to_list()

        if in_stock_only:
            products = [p for p in products if p.stock.available - p.stock.reserved > 0]

        products = products[:limit]
        views = [_to_view(p) for p in products]
        return SearchPage(items=views, total=len(views), limit=limit)

    async def get(self, sku: str) -> ProductView:
        product = await Product.get(sku)
        if product is None or not product.active:
            raise product_not_found(sku)
        return _to_view(product)

    def manifest(self) -> dict:
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
            },
            "product_schema": ProductView.model_json_schema(),
            "policy_limits": {
                "max_order_paise": 2500000,
                "denied_categories": ["gift_card", "crypto", "alcohol", "tobacco"],
                "currency": "INR",
            },
        }


catalog_service = CatalogService()
