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
