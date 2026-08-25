import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

_POLICY_PATH = Path(__file__).parent / "policy.yaml"


@dataclass(frozen=True)
class Limits:
    max_order_paise: int
    approval_threshold_paise: int
    max_qty_per_line: int
    max_lines_per_cart: int
    session_24h_spend_paise: int
    max_orders_per_hour: int


@dataclass(frozen=True)
class BuyerAgentPolicy:
    require_mandate: bool
    max_order_paise: int


@dataclass(frozen=True)
class Policy:
    version: str
    yaml_hash: str
    limits: Limits
    deny_categories: frozenset[str]
    allow_categories: frozenset[str]
    allowed_currencies: frozenset[str]
    approval_ttl_minutes: int
    buyer_agent: BuyerAgentPolicy


def load_policy(path: Path = _POLICY_PATH) -> Policy:
    """Policy is versioned data, not code — every evaluation records
    `policy_version` and `yaml_hash`, so a judge can tie any verdict to an
    exact policy revision."""
    raw_text = path.read_text()
    data = yaml.safe_load(raw_text)
    yaml_hash = "sha256:" + hashlib.sha256(raw_text.encode()).hexdigest()
    limits = data["limits"]
    return Policy(
        version=data["version"],
        yaml_hash=yaml_hash,
        limits=Limits(
            max_order_paise=limits["max_order_paise"],
            approval_threshold_paise=limits["approval_threshold_paise"],
            max_qty_per_line=limits["max_qty_per_line"],
            max_lines_per_cart=limits["max_lines_per_cart"],
            session_24h_spend_paise=limits["session_24h_spend_paise"],
            max_orders_per_hour=limits["max_orders_per_hour"],
        ),
        deny_categories=frozenset(data["categories"]["deny"]),
        allow_categories=frozenset(data["categories"].get("allow") or []),
        allowed_currencies=frozenset(data["currency"]["allowed"]),
        approval_ttl_minutes=data["approval"]["ttl_minutes"],
        buyer_agent=BuyerAgentPolicy(
            require_mandate=data["buyer_agent"]["require_mandate"],
            max_order_paise=data["buyer_agent"]["max_order_paise"],
        ),
    )


policy = load_policy()
