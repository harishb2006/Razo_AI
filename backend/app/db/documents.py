from beanie import Document
from pydantic import BaseModel, Field


class StockInfo(BaseModel):
    available: int
    reserved: int = 0


class Product(Document):
    id: str  # SKU is the natural key; Beanie aliases `id` to `_id`
    title: str
    description: str = ""
    category: str
    brand: str = ""
    price_paise: int
    currency: str = "INR"
    attributes: dict = Field(default_factory=dict)
    stock: StockInfo
    search_text: str
    active: bool = True
    version: int = 1
    updated_at: str
    created_at: str

    class Settings:
        name = "products"
        use_state_management = True
