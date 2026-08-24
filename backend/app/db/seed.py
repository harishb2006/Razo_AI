"""Shared seeding logic — used by scripts/seed_catalog.py (CLI, real/offline DB)
and by db/client.py to auto-seed OFFLINE_MODE on startup, since an in-memory
mongomock store does not persist across the separate seed-script process and
the server process."""
import random
from datetime import datetime, timezone

from app.db.documents import Product, StockInfo

CATEGORIES = {
    "footwear": ["Trailrunner", "Streetform", "Aerowalk", "Gripline"],
    "apparel": ["Windshell", "Baselayer", "Trekpant", "Duracap"],
    "electronics": ["Pulseband", "Ecobuds", "Snapcam", "Flexlamp"],
    "home": ["Warmthrow", "Brewkettle", "Glowlantern", "Softmat"],
    "sports": ["Gripball", "Flexmat", "Powerband", "Coreroller"],
    "books": ["Fieldnotes", "Trailmaps", "Craftguide", "Mindsketch"],
    "beauty": ["Purebalm", "Glowmist", "Softcream", "Cleanbar"],
    "grocery": ["Roastbeans", "Purehoney", "Wholegrain", "Coldbrew"],
}
BRANDS = ["Vaayu", "Norrin", "Kestra", "Amble", "Fielden"]
COLOURS = ["blue", "black", "grey", "olive", "red"]


def _sku(category: str, idx: int) -> str:
    return f"RZ-{category[:4].upper()}-{100 + idx}"


def build_products() -> list[Product]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rng = random.Random(42)  # deterministic across runs
    idx = 0
    docs = []
    for category, names in CATEGORIES.items():
        for name in names:
            for variant in range(2):
                idx += 1
                brand = rng.choice(BRANDS)
                colour = rng.choice(COLOURS)
                title = f"{name} {['X', 'Pro', 'Lite', 'Plus'][variant % 4]}"
                price = rng.randint(499, 12999) * 100
                sku = _sku(category, idx)
                search_text = " ".join(
                    [title, brand, category, colour, "running" if category == "footwear" else ""]
                ).lower()
                docs.append(
                    Product(
                        id=sku,
                        title=title,
                        description=f"{title} by {brand} — a {category} essential.",
                        category=category,
                        brand=brand,
                        price_paise=price,
                        currency="INR",
                        attributes={"colour": colour, "tags": [category]},
                        stock=StockInfo(available=rng.randint(5, 50), reserved=0),
                        search_text=search_text,
                        active=True,
                        version=1,
                        updated_at=now,
                        created_at=now,
                    )
                )
    return docs


async def seed_products(force: bool = False) -> int:
    if not force:
        existing = await Product.find_all().count()
        if existing > 0:
            return existing
    await Product.find_all().delete()
    docs = build_products()
    await Product.insert_many(docs)
    return len(docs)
