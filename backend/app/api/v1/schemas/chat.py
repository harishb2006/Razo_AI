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


class FindingView(BaseModel):
    rule_id: str
    outcome: str
    reason: str
    observed: object = None
    limit: object = None


class PolicyView(BaseModel):
    """Rendered verbatim by the UI. The frontend computes no limits and no
    eligibility of its own — what appears on screen is provably the server's
    decision, not a UI approximation of it."""

    decision: str  # ALLOW | REQUIRE_APPROVAL | DENY
    reason_summary: str
    findings: list[FindingView] = []
    violations: list[FindingView] = []


class NextAction(BaseModel):
    type: str  # payment_link | awaiting_approval | none
    payment_link_url: str | None = None
    approval_id: str | None = None
    expires_at: str | None = None
    order_id: str | None = None


class SuggestionView(BaseModel):
    """A growth suggestion the buyer can accept with one click. Every field is
    a live catalog value — the UI renders it, it never composes an offer."""

    sku: str
    title: str
    price_paise: int
    price_display: str
    category: str
    why: str


class ProductOffer(BaseModel):
    """A catalog row the assistant just showed, rendered by the UI as a row
    with an Add button. Values come straight from the catalog of record."""

    sku: str
    title: str
    brand: str = ""
    category: str = ""
    price_paise: int
    price_display: str
    in_stock: bool = True


class TurnResponse(BaseModel):
    session_id: str
    turn: int
    mode: str  # "normal" | "degraded"
    reply: str
    cart: CartView
    latency_ms: int
    policy: PolicyView | None = None
    next_action: NextAction | None = None
    suggestions: list[SuggestionView] = []
    products: list[ProductOffer] = []
    trace_id: str | None = None


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
