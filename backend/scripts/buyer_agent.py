"""An AI buyer: a stranger's program that shops this merchant end to end.

It is deliberately *not* part of the shop. It knows one URL and nothing else —
no SKUs, no endpoint paths, no prices. Everything it needs it discovers from
`/.well-known/agent-catalog.json`, which is the point: if the manifest is
honest, a machine that has never seen this shop can buy from it.

It carries a mandate — a budget and a list of categories it is permitted to
buy — and the merchant's rulebook holds it to that mandate more strictly than
it holds a human (R10, plus a lower ceiling than a person gets).

    python -m scripts.buyer_agent [--base-url http://127.0.0.1:8000]

The refusals matter more than the purchase. A machine buyer being correctly
stopped is the whole thesis of this project in one screen.
"""
import argparse
import asyncio
import sys

import httpx

API_KEY = "dev-local-key"


class Shop:
    """The merchant, as seen by software that has only read the manifest."""

    def __init__(self, client: httpx.AsyncClient, manifest: dict):
        self._client = client
        self._manifest = manifest
        self._endpoints = manifest["endpoints"]

    @classmethod
    async def discover(cls, client: httpx.AsyncClient) -> "Shop":
        r = await client.get("/.well-known/agent-catalog.json")
        r.raise_for_status()
        return cls(client, r.json())

    @property
    def name(self) -> str:
        return self._manifest.get("name", "unknown merchant")

    @property
    def limits(self) -> dict:
        return self._manifest.get("limits") or {}

    def _route(self, key: str, **params) -> tuple[str, str]:
        method, path = self._endpoints[key].split(" ", 1)
        for name, value in params.items():
            path = path.replace("{" + name + "}", str(value))
        return method, "/api/v1" + path

    async def categories(self) -> list[dict]:
        _, url = self._route("categories")
        return (await self._client.get(url)).json()

    async def resolve(self, instruction: str) -> list[dict]:
        _, url = self._route("resolve")
        body = (await self._client.post(url, json={"query": instruction})).json()
        return body.get("matches", [])

    async def open_session(self, mandate: dict) -> str:
        r = await self._client.post(
            "/api/v1/chat/sessions",
            json={"channel": "buyer_agent", "actor_ref": "razo-demo-buyer", "mandate": mandate},
        )
        return r.json()["session_id"]

    async def add(self, session_id: str, sku: str, qty: int) -> dict:
        return (await self._client.post(
            f"/api/v1/cart/{session_id}/items", json={"sku": sku, "qty": qty},
        )).json()

    async def checkout(self, session_id: str) -> dict:
        _, url = self._route("checkout", session_id=session_id)
        return (await self._client.post(url)).json()


async def attempt(shop: Shop, label: str, instruction: str, mandate: dict, qty: int = 1) -> str:
    """One shopping attempt, start to finish, reported honestly."""
    print(f"\n\033[1m{label}\033[0m")
    print(f'  instruction : "{instruction}"')
    budget = mandate.get("budget_paise")
    print(f"  mandate     : budget ₹{budget / 100:,.0f}" if budget else "  mandate     : (none)", end="")
    print(f" · categories {mandate.get('allowed_categories') or 'any'}")

    matches = await shop.resolve(instruction)
    if not matches:
        print("  → the shop has nothing matching that instruction.")
        return "no_match"

    pick = matches[0]
    print(f"  picked      : {pick['title']} ({pick['sku']}) at {pick['price_display']} × {qty}")

    session_id = await shop.open_session(mandate)
    await shop.add(session_id, pick["sku"], qty)
    result = await shop.checkout(session_id)

    status = result.get("status", "error")
    colour = {"paid_link_created": "\033[32m", "approval_required": "\033[33m"}.get(status, "\033[31m")
    print(f"  verdict     : {colour}{status.upper()}\033[0m")
    print(f"  because     : {result.get('reason', result)}")

    # The rulebook's own words, for the rule that governs autonomous buyers.
    for finding in result.get("findings", []):
        if finding["rule_id"] == "R10" and finding["outcome"] != "pass":
            print(f"  R10         : {finding['reason']}")
    if link := result.get("payment_link_url"):
        print(f"  payment     : {link}")
    return status


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    async with httpx.AsyncClient(
        base_url=args.base_url, headers={"X-API-Key": API_KEY}, timeout=30.0,
    ) as client:
        try:
            shop = await Shop.discover(client)
        except (httpx.HTTPError, KeyError) as e:
            print(f"Could not read the shop's manifest at {args.base_url}: {e}")
            print("Is the backend running?  make run")
            return 1

        print(f"\033[1mDiscovered:\033[0m {shop.name}")
        print(f"  endpoints it advertises : {', '.join(shop._endpoints)}")
        cats = await shop.categories()
        print(f"  departments it stocks   : {', '.join(c['category'] for c in cats)}")
        if limits := shop.limits:
            print(f"  limits it publishes     : {limits}")

        footwear_mandate = {
            "budget_paise": 1000000,
            "allowed_categories": ["footwear", "apparel"],
            "max_items": 3,
            "purpose": "Replace worn running shoes",
        }

        results = {
            "inside": await attempt(
                shop, "1 · Inside its mandate",
                "running shoes", footwear_mandate,
            ),
            "category": await attempt(
                shop, "2 · Outside its permitted categories",
                "wireless earbuds", footwear_mandate,
            ),
            "budget": await attempt(
                shop, "3 · Beyond the budget it was given",
                "running shoes",
                {**footwear_mandate, "budget_paise": 100000}, qty=10,
            ),
            "no_mandate": await attempt(
                shop, "4 · With no mandate at all",
                "running shoes", {},
            ),
        }

        print("\n\033[1mWhat the merchant's rulebook did\033[0m")
        for name, status in results.items():
            print(f"  {name:11} → {status}")
        refused = [n for n, s in results.items() if s == "denied"]
        print(f"\n  {len(refused)} of 4 attempts refused outright, by code the AI cannot argue with.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
