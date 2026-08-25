from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProductSnapshot:
    """Current catalog truth for one SKU, read *before* evaluate() is called
    so the engine itself stays free of I/O."""
    price_paise: int
    version: int
    available: int
    active: bool


@dataclass(frozen=True)
class RuleContext:
    catalog_snapshot: dict[str, ProductSnapshot]
    session_24h_spend_paise: int
    orders_last_hour: int
    merchant_approved: bool = False
    """Set only when a merchant has already approved *this* cart in the
    inbox. It satisfies the two rules that escalate (R2, R7) and nothing
    else — a DENY still denies, which is the point of re-evaluating at all."""


@dataclass(frozen=True)
class Finding:
    rule_id: str
    outcome: Literal["pass", "require_approval", "deny"]
    reason: str
    observed: object
    limit: object


@dataclass(frozen=True)
class VerdictToken:
    token: str
    expires_at: str


@dataclass(frozen=True)
class Verdict:
    decision: Literal["ALLOW", "REQUIRE_APPROVAL", "DENY"]
    findings: tuple[Finding, ...]
    reason_summary: str
    policy_version: str
    intent_hash: str
    evaluation_id: str
    token: VerdictToken | None = None
