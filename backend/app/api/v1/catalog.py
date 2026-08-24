from fastapi import APIRouter, Query

from app.api.v1.schemas.catalog import ProductView, SearchPage
from app.services.catalog_service import catalog_service

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products", response_model=SearchPage)
async def search_products(
    q: str | None = None,
    category: str | None = None,
    price_max_paise: int | None = None,
    in_stock: bool = False,
    limit: int = Query(10, le=50),
):
    return await catalog_service.search(
        q=q, category=category, price_max_paise=price_max_paise,
        in_stock_only=in_stock, limit=limit,
    )


@router.get("/products/{sku}", response_model=ProductView)
async def get_product(sku: str):
    return await catalog_service.get(sku)


@router.get("/schema")
async def get_schema():
    return ProductView.model_json_schema()
