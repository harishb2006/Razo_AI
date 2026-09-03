from fastapi import APIRouter, Query
from pydantic import BaseModel

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


class ResolveRequest(BaseModel):
    query: str
    limit: int = 3


@router.get("/categories")
async def list_categories():
    """Advertised by the agent manifest — an AI buyer reads this to check its
    mandate's categories against what the shop actually stocks."""
    return await catalog_service.categories()


@router.post("/resolve")
async def resolve(req: ResolveRequest):
    """Advertised by the agent manifest — turns "running shoes" into SKUs an
    agent can actually put in a cart."""
    return await catalog_service.resolve(req.query, limit=req.limit)


@router.get("/schema")
async def get_schema():
    return ProductView.model_json_schema()
