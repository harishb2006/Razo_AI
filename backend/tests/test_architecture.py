"""The boundedness claim, enforced rather than asserted: the agent layer has
no import path to the payments layer, and the only way anything reaches
Razorpay is through a signed ALLOW verdict.

This is the test to point at when someone asks 'show me the line that stops
the AI over-spending' — it reads the source, so it cannot drift from it."""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_agent_layer_cannot_import_the_payments_layer():
    offenders = {
        path.relative_to(APP).as_posix(): sorted(m for m in imports_of(path) if m.startswith("app.payments"))
        for path in (APP / "agent").rglob("*.py")
        if any(m.startswith("app.payments") for m in imports_of(path))
    }
    assert offenders == {}, f"agent/** must not import payments/**: {offenders}"


def test_the_policy_engine_touches_no_io():
    """The rulebook is only provable offline if it imports nothing that could
    reach a network, a database, or an LLM."""
    forbidden = ("httpx", "motor", "beanie", "app.db", "app.payments", "app.agent", "app.services")
    offenders = {}
    for path in (APP / "policy").rglob("*.py"):
        bad = sorted(m for m in imports_of(path) if m.startswith(forbidden))
        if bad:
            offenders[path.relative_to(APP).as_posix()] = bad
    assert offenders == {}, f"policy/** must stay pure: {offenders}"


def test_razorpay_is_only_reachable_through_the_payments_service():
    offenders = [
        path.relative_to(APP).as_posix()
        for path in APP.rglob("*.py")
        if "razorpay_client" in " ".join(imports_of(path))
        and path.parent.name != "payments"
    ]
    assert offenders == [], f"only payments/** may import the Razorpay client: {offenders}"


def test_no_tool_exposed_to_the_model_accepts_a_price():
    """Tenet T2 at the type level: if no tool can carry a price, there is no
    code path for a model-invented price to reach the cart."""
    from app.agent.tools.registry import TOOLS

    offenders = {
        name: [f for f in spec.args_model.model_fields if "price" in f and f != "price_max_paise"]
        for name, spec in TOOLS.items()
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"no tool may accept a price: {offenders}"


def test_check_policy_never_hands_the_model_a_signed_token():
    """A dry-run verdict must not be usable to authorize a payment."""
    source = (APP / "agent" / "tools" / "registry.py").read_text()
    assert "include_token=True" not in source
