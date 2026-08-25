from app.domain.intent import OrderIntent
from app.domain.money import inr
from app.policy.policy import Policy
from app.policy.types import Finding, RuleContext


def rule_r1_hard_cap(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    limit = policy.limits.max_order_paise
    if intent.total_paise > limit:
        return Finding(
            "R1", "deny",
            f"Order total {inr(intent.total_paise)} exceeds the hard per-order cap of {inr(limit)}.",
            intent.total_paise, limit,
        )
    return Finding(
        "R1", "pass",
        f"Order total {inr(intent.total_paise)} is within the {inr(limit)} per-order cap.",
        intent.total_paise, limit,
    )


def rule_r2_approval_threshold(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    limit = policy.limits.approval_threshold_paise
    if intent.total_paise >= limit:
        if ctx.merchant_approved:
            return Finding(
                "R2", "pass",
                f"Order total {inr(intent.total_paise)} is above the {inr(limit)} auto-approve threshold, "
                "but the merchant has approved this cart.",
                intent.total_paise, limit,
            )
        return Finding(
            "R2", "require_approval",
            f"Order total {inr(intent.total_paise)} is at or above the {inr(limit)} auto-approve threshold.",
            intent.total_paise, limit,
        )
    return Finding(
        "R2", "pass",
        f"Order total {inr(intent.total_paise)} is below the {inr(limit)} auto-approve threshold.",
        intent.total_paise, limit,
    )


def rule_r3_category_denylist(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    denied = sorted({l.category for l in intent.lines if l.category in policy.deny_categories})
    if denied:
        return Finding("R3", "deny", f"Category not allowed: {', '.join(denied)}.", ", ".join(denied), "none")
    return Finding("R3", "pass", "No denied categories in the cart.", "-", "-")


def rule_r4_line_qty_cap(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    limit = policy.limits.max_qty_per_line
    over = [l for l in intent.lines if l.qty > limit]
    if over:
        skus = ", ".join(f"{l.sku} (qty {l.qty})" for l in over)
        return Finding(
            "R4", "deny", f"Quantity exceeds the per-line limit of {limit}: {skus}.",
            max(l.qty for l in over), limit,
        )
    return Finding("R4", "pass", f"All line quantities are within the {limit}-unit limit.", "-", limit)


def rule_r5_stock(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    short = [
        l.sku for l in intent.lines
        if (snap := ctx.catalog_snapshot.get(l.sku)) is None or not snap.active or snap.available < l.qty
    ]
    if short:
        return Finding("R5", "deny", f"Out of stock or unavailable: {', '.join(short)}.", len(short), 0)
    return Finding("R5", "pass", "All lines are in stock.", 0, 0)


def rule_r6_price_integrity(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    drifted = [
        l.sku for l in intent.lines
        if (snap := ctx.catalog_snapshot.get(l.sku)) is None
        or snap.price_paise != l.unit_price_paise
        or snap.version != l.product_version
    ]
    if drifted:
        return Finding(
            "R6", "deny",
            f"Price or product version changed since quoting for: {', '.join(drifted)}. Re-quote required.",
            len(drifted), 0,
        )
    return Finding("R6", "pass", "All line prices match the catalog of record.", 0, 0)


def rule_r7_spend_velocity(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    limit = policy.limits.session_24h_spend_paise
    projected = ctx.session_24h_spend_paise + intent.total_paise
    if projected >= limit:
        if ctx.merchant_approved:
            return Finding(
                "R7", "pass",
                f"24-hour spend of {inr(projected)} is above the {inr(limit)} limit, "
                "but the merchant has approved this cart.",
                projected, limit,
            )
        return Finding(
            "R7", "require_approval",
            f"Adding this order brings your 24-hour spend to {inr(projected)}, at or above the {inr(limit)} limit.",
            projected, limit,
        )
    return Finding(
        "R7", "pass",
        f"Projected 24-hour spend of {inr(projected)} is within the {inr(limit)} limit.", projected, limit,
    )


def rule_r8_order_frequency(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    limit = policy.limits.max_orders_per_hour
    projected = ctx.orders_last_hour + 1
    if projected > limit:
        return Finding(
            "R8", "deny", f"This would be order {projected} in the last hour, above the limit of {limit}.",
            projected, limit,
        )
    return Finding("R8", "pass", f"Order frequency is within the {limit}-per-hour limit.", projected, limit)


def rule_r9_currency(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    if intent.currency not in policy.allowed_currencies:
        allowed = ", ".join(sorted(policy.allowed_currencies))
        return Finding("R9", "deny", f"Currency {intent.currency} is not accepted.", intent.currency, allowed)
    return Finding("R9", "pass", f"Currency {intent.currency} is accepted.", intent.currency, intent.currency)


def rule_r10_buyer_agent_mandate(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    if intent.channel != "buyer_agent":
        return Finding("R10", "pass", "Not a buyer-agent session.", "-", "-")

    mandate = intent.mandate or {}
    if policy.buyer_agent.require_mandate and not mandate:
        return Finding("R10", "deny", "Buyer-agent session has no mandate on file.", "none", "required")

    budget = mandate.get("budget_paise")
    limit = min(policy.buyer_agent.max_order_paise, budget) if budget else policy.buyer_agent.max_order_paise
    if intent.total_paise > limit:
        return Finding(
            "R10", "deny", f"Order total {inr(intent.total_paise)} exceeds the buyer-agent limit of {inr(limit)}.",
            intent.total_paise, limit,
        )

    allowed_categories = set(mandate.get("allowed_categories") or [])
    if allowed_categories:
        out_of_scope = sorted({l.category for l in intent.lines if l.category not in allowed_categories})
        if out_of_scope:
            scope = sorted(allowed_categories)
            return Finding(
                "R10", "deny",
                f"Cart category '{out_of_scope[0]}' is outside the mandate scope {scope}.",
                out_of_scope[0], scope,
            )

    return Finding("R10", "pass", "Cart is within the buyer-agent's mandate.", intent.total_paise, limit)


def rule_r11_cart_integrity(intent: OrderIntent, policy: Policy, ctx: RuleContext) -> Finding:
    computed_total = sum(l.line_total_paise for l in intent.lines)
    if computed_total != intent.total_paise:
        return Finding(
            "R11", "deny",
            f"Cart total {inr(intent.total_paise)} does not match the sum of its lines {inr(computed_total)}.",
            intent.total_paise, computed_total,
        )
    for l in intent.lines:
        expected = l.unit_price_paise * l.qty
        if expected != l.line_total_paise:
            return Finding(
                "R11", "deny",
                f"Line total for {l.sku} does not match unit price × quantity.", l.line_total_paise, expected,
            )
    return Finding("R11", "pass", "Cart total matches the sum of its lines.", intent.total_paise, computed_total)


RULES = [
    rule_r1_hard_cap,
    rule_r2_approval_threshold,
    rule_r3_category_denylist,
    rule_r4_line_qty_cap,
    rule_r5_stock,
    rule_r6_price_integrity,
    rule_r7_spend_velocity,
    rule_r8_order_frequency,
    rule_r9_currency,
    rule_r10_buyer_agent_mandate,
    rule_r11_cart_integrity,
]
