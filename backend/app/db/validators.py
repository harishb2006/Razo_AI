from pymongo.errors import OperationFailure

PRODUCTS_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id", "title", "category", "price_paise", "currency",
            "stock", "search_text", "active", "version",
        ],
        "properties": {
            "price_paise": {"bsonType": "long", "minimum": 0},
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


async def apply_validators(db):
    """Apply $jsonSchema validators. Best-effort: an Atlas M0 free-tier
    cluster does support collMod validators, but this is skipped entirely
    in OFFLINE_MODE since mongomock does not enforce them."""
    collection_names = await db.list_collection_names()
    if "products" not in collection_names:
        await db.create_collection("products")
    try:
        await db.command({
            "collMod": "products",
            "validator": PRODUCTS_SCHEMA,
            "validationAction": "error",
            "validationLevel": "strict",
        })
    except OperationFailure:
        pass
