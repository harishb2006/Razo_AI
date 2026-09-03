from pymongo.errors import OperationFailure

PRODUCTS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id", "title", "category", "price_paise", "currency",
            "stock", "search_text", "active", "version",
        ],
        "properties": {
            # ["long", "int"], not "long": pymongo encodes a Python int below
            # 2^31 as int32, so a strict "long" rejects every realistic price.
            "price_paise": {"bsonType": ["long", "int"], "minimum": 0},
            "currency": {"bsonType": "string"},
            "active": {"bsonType": "bool"},
            "version": {"bsonType": "int", "minimum": 1},
            "stock": {
                "bsonType": "object",
                "required": ["available", "reserved"],
                "properties": {
                    "available": {"bsonType": "int", "minimum": 0},
                    "reserved": {"bsonType": "int", "minimum": 0},
                },
            },
        },
    }
}


ORDERS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "session_id", "evaluation_id", "amount_paise", "idempotency_key", "state"],
        "properties": {
            "amount_paise": {"bsonType": ["long", "int"], "minimum": 1},
            "evaluation_id": {"bsonType": "string", "minLength": 1},
        },
    }
}


async def apply_validators(db):
    """Apply $jsonSchema validators. Best-effort: an Atlas M0 free-tier
    cluster does support collMod validators, but this is skipped entirely
    in OFFLINE_MODE since mongomock does not enforce them."""
    collection_names = await db.list_collection_names()
    for name in ("products", "orders"):
        if name not in collection_names:
            await db.create_collection(name)

    for name, schema in (("products", PRODUCTS_SCHEMA), ("orders", ORDERS_SCHEMA)):
        try:
            await db.command({
                "collMod": name,
                "validator": schema,
                "validationAction": "error",
                "validationLevel": "strict",
            })
        except OperationFailure:
            pass
