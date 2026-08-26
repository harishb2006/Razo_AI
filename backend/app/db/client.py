from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db.documents import (
    Approval, AuditEvent, Counter, EvalRun, LLMCall, Message, Order, Payment, Product, Session,
)

_client: AsyncIOMotorClient | None = None
_audit_client: AsyncIOMotorClient | None = None

DOCUMENT_MODELS = [
    Product, Session, Message, Order, Payment, Approval, AuditEvent, Counter, LLMCall, EvalRun,
]


def _make_client():
    if settings.offline_mode:
        from mongomock_motor import AsyncMongoMockClient

        return AsyncMongoMockClient()
    return AsyncIOMotorClient(
        settings.mongodb_uri,
        maxPoolSize=settings.mongo_max_pool_size,
        serverSelectionTimeoutMS=5000,
    )


def _make_audit_client():
    """The audit writer connects as a *second* Atlas user holding a custom
    role with only `find` and `insert` on `audit_events` — it has no update
    or delete privilege at all, so history cannot be quietly edited even by
    a bug in this codebase. Falls back to the app client when no separate
    URI is configured (offline, CI, and single-user local runs)."""
    if settings.offline_mode or not settings.mongodb_audit_uri:
        return None
    return AsyncIOMotorClient(
        settings.mongodb_audit_uri,
        maxPoolSize=settings.mongo_max_pool_size,
        serverSelectionTimeoutMS=5000,
    )


async def connect():
    global _client, _audit_client
    _client = _make_client()
    _audit_client = _make_audit_client()
    db = _client[settings.mongodb_db]
    await init_beanie(database=db, document_models=DOCUMENT_MODELS)

    from app.db.validators import apply_validators
    from app.db.indexes import apply_indexes

    if not settings.offline_mode:
        await apply_validators(db)
    await apply_indexes(db)

    if settings.offline_mode:
        from app.db.seed import seed_products

        await seed_products()

    # Snapshot the catalog so browsing survives a Mongo outage (F11).
    from app.services.catalog_service import catalog_service

    await catalog_service.load_snapshot()


async def disconnect():
    if _client is not None:
        _client.close()
    if _audit_client is not None:
        _audit_client.close()


def get_client() -> AsyncIOMotorClient:
    assert _client is not None, "DB client not initialised — call connect() first"
    return _client


def get_app_db():
    return get_client()[settings.mongodb_db]


def get_audit_collection():
    """Writes go through the restricted client when one is configured; reads
    elsewhere use the Beanie `AuditEvent` model on the app connection."""
    client = _audit_client or get_client()
    return client[settings.mongodb_db]["audit_events"]
