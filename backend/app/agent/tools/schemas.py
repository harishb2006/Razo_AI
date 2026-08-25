from pydantic import BaseModel, Field


class SearchCatalogArgs(BaseModel):
    query: str | None = None
    category: str | None = None
    price_max_paise: int | None = None
    limit: int = Field(default=5, le=10, gt=0)


class GetProductArgs(BaseModel):
    sku: str


class AddToCartArgs(BaseModel):
    sku: str
    qty: int = Field(gt=0, le=10)


class UpdateCartItemArgs(BaseModel):
    sku: str
    qty: int = Field(ge=0, le=10)


class GetCartArgs(BaseModel):
    pass


class CheckPolicyArgs(BaseModel):
    pass


class RequestCheckoutArgs(BaseModel):
    confirm: bool = True
