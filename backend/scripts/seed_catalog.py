"""Seed ~60 products across 8 categories. Run: python -m scripts.seed_catalog"""
import asyncio

from app.db.client import connect, disconnect
from app.db.seed import seed_products


async def main():
    await connect()
    count = await seed_products(force=True)
    print(f"Seeded {count} products across 8 categories.")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
