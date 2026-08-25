from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    channel: str = "human_chat"
    actor_ref: str | None = None
    mandate: dict | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


class SendMessageRequest(BaseModel):
    text: str


class CartItemView(BaseModel):
    sku: str
    qty: int
    unit_price_paise: int
    line_total_paise: int


class CartView(BaseModel):
    version: int
    state: str
    items: list[CartItemView]
    subtotal_paise: int
    total_paise: int
    currency: str


class TurnResponse(BaseModel):
    session_id: str
    turn: int
    mode: str  # "normal" | "degraded"
    reply: str
    cart: CartView
    latency_ms: int


class SessionView(BaseModel):
    session_id: str
    channel: str
    state: str
    turn_count: int
    cart: CartView


class MessageView(BaseModel):
    turn: int
    role: str
    content: str
    tool_name: str | None = None
