from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db.documents import Approval, Message, Order, Payment, Product, Session

_client: AsyncIOMotorClient | None = None


def _make_client():
    if settings.offline_mode:
        from mongomock_motor import AsyncMongoMockClient

        return AsyncMongoMockClient()
    return AsyncIOMotorClient(
        settings.mongodb_uri,
        maxPoolSize=settings.mongo_max_pool_size,
        serverSelectionTimeoutMS=5000,
    )


async def connect():
    global _client
    _client = _make_client()
    db = _client[settings.mongodb_db]
    await init_beanie(database=db, document_models=[Product, Session, Message, Order, Payment, Approval])

    from app.db.validators import apply_validators
    from app.db.indexes import apply_indexes

    if not settings.offline_mode:
        await apply_validators(db)
    await apply_indexes(db)

    if settings.offline_mode:
        from app.db.seed import seed_products

        await seed_products()


async def disconnect():
    if _client is not None:
        _client.close()


def get_client() -> AsyncIOMotorClient:
    assert _client is not None, "DB client not initialised — call connect() first"
    return _client
