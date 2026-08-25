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


class CartItem(BaseModel):
    sku: str
    qty: int
    unit_price_paise: int
    product_version: int
    category: str
    line_total_paise: int


class Cart(BaseModel):
    version: int = 0
    state: str = "open"  # open | locked | ordered | released
    items: list[CartItem] = Field(default_factory=list)
    subtotal_paise: int = 0
    total_paise: int = 0
    currency: str = "INR"
    updated_at: str = ""


class Session(Document):
    id: str  # ULID, set by the caller; Beanie aliases `id` to `_id`
    channel: str = "human_chat"  # human_chat | buyer_agent
    actor_ref: str | None = None
    mandate: dict | None = None
    state: str = "active"  # active | awaiting_approval | completed | abandoned | failed
    turn_count: int = 0
    cart: Cart = Field(default_factory=Cart)
    created_at: str
    closed_at: str | None = None

    class Settings:
        name = "sessions"


class Message(Document):
    session_id: str
    turn: int
    role: str  # user | assistant | tool
    content: str
    tool_name: str | None = None
    tool_args: dict | None = None
    created_at: str

    class Settings:
        name = "messages"


class Order(Document):
    id: str  # ULID
    session_id: str
    actor_key: str  # session.actor_ref or session_id — what R7/R8 rate limits against
    evaluation_id: str
    razorpay_order_id: str | None = None
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    amount_paise: int
    currency: str = "INR"
    state: str = "creating"  # creating | link_sent | paid | failed | upstream_failed | expired
    idempotency_key: str
    failure_code: str | None = None
    created_at: str
    updated_at: str

    class Settings:
        name = "orders"


class Payment(Document):
    id: str  # ULID
    order_id: str
    razorpay_payment_id: str | None = None
    status: str  # captured | failed
    method: str | None = None
    amount_paise: int
    error_code: str | None = None
    error_description: str | None = None
    raw_event: dict = Field(default_factory=dict)
    created_at: str

    class Settings:
        name = "payments"


class Approval(Document):
    id: str  # ULID
    session_id: str
    evaluation_id: str
    intent_hash: str  # binds the approval to one exact cart, so it can't be widened afterwards
    amount_paise: int
    state: str = "pending"  # pending | approved | rejected | expired
    reason: str
    decided_by: str | None = None
    decided_at: str | None = None
    expires_at: str
    created_at: str

    class Settings:
        name = "approvals"
