from app.config import settings


async def apply_indexes(db):
    """Applied idempotently at startup so a cold clone needs no manual setup.
    Text index creation is skipped in OFFLINE_MODE — mongomock's text-index
    support doesn't match real MongoDB, so CatalogService falls back to a
    regex scan when offline instead."""
    if not settings.offline_mode:
        await db.products.create_index(
            [("search_text", "text"), ("title", "text"), ("brand", "text")]
        )
    await db.products.create_index([("category", 1), ("active", 1), ("price_paise", 1)])
    await db.messages.create_index([("session_id", 1), ("turn", 1)])
    await db.sessions.create_index([("state", 1), ("created_at", -1)])
    await db.orders.create_index([("idempotency_key", 1)], unique=True)
    await db.orders.create_index([("actor_key", 1), ("created_at", -1)])
    await db.orders.create_index([("session_id", 1), ("created_at", -1)])
    await db.payments.create_index([("order_id", 1)])
    await db.payments.create_index([("razorpay_payment_id", 1)], unique=True, sparse=True)
    await db.approvals.create_index([("state", 1), ("expires_at", 1)])
