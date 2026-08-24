from pydantic import BaseModel


class ProductView(BaseModel):
    sku: str
    title: str
    description: str
    category: str
    brand: str
    price_paise: int
    price_display: str
    currency: str
    in_stock: bool
    qty_available: int
    attributes: dict
    version: int
    updated_at: str


class SearchPage(BaseModel):
    items: list[ProductView]
    total: int
    limit: int
    cursor: str | None = None
