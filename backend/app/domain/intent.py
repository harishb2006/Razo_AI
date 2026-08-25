import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class IntentLine:
    sku: str
    qty: int
    unit_price_paise: int
    product_version: int
    category: str
    line_total_paise: int


@dataclass(frozen=True)
class OrderIntent:
    """Built only from persisted documents — never from anything the model said.
    `hash()` is what the policy engine's verdict and the payment service's
    verdict token are bound to, so a cart mutated after evaluation cannot be
    executed against a stale ALLOW."""

    session_id: str
    cart_version: int
    channel: str  # "human_chat" | "buyer_agent"
    lines: tuple[IntentLine, ...]
    total_paise: int
    currency: str
    mandate: dict | None
    created_at: str

    def canonical_json(self) -> str:
        payload = {
            "session_id": self.session_id,
            "cart_version": self.cart_version,
            "channel": self.channel,
            "lines": [
                {
                    "sku": l.sku, "qty": l.qty, "unit_price_paise": l.unit_price_paise,
                    "product_version": l.product_version, "category": l.category,
                    "line_total_paise": l.line_total_paise,
                }
                for l in self.lines
            ],
            "total_paise": self.total_paise,
            "currency": self.currency,
            "mandate": self.mandate,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    @property
    def skus(self) -> list[str]:
        return [l.sku for l in self.lines]
