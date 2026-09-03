"""Shoppers do not speak in catalog categories.

The seed tags a running shoe "footwear" and never once says "shoe", so a
literal search for what people actually type used to come back empty and the
assistant told them the shop was bare. These lock in the vocabulary bridge.
"""
import pytest

from app.db.documents import Product, StockInfo
from app.services.catalog_service import _infer_category, catalog_service


async def _seed(**overrides):
    defaults = dict(
        title="Trailrunner Pro", description="Trailrunner Pro by Norrin — a footwear essential.",
        category="footwear", brand="Norrin", price_paise=278500, currency="INR",
        attributes={"colour": "black", "tags": ["footwear"]},
        stock=StockInfo(available=10, reserved=0),
        search_text="trailrunner pro norrin footwear black running",
        active=True, version=1, updated_at="2026-08-29T00:00:00Z", created_at="2026-08-29T00:00:00Z",
    )
    defaults.update(overrides)
    await Product(**defaults).insert()


@pytest.mark.parametrize(
    "word,expected",
    [
        ("shoe", "footwear"), ("shoes", "footwear"), ("sneakers", "footwear"),
        ("i want shoe under 3000", "footwear"),
        ("air buds", "electronics"), ("headphones", "electronics"),
        ("a book please", "books"),
        ("something nice", None),
    ],
)
def test_a_shoppers_word_maps_onto_our_category(word, expected):
    assert _infer_category(word) == expected


@pytest.mark.asyncio
async def test_asking_for_a_shoe_finds_footwear(db):
    """"shoe" appears nowhere in the catalog text — only the category does."""
    await _seed(id="RZ-FOOT-102")

    page = await catalog_service.search(q="i want shoe under 3000", price_max_paise=300000)

    assert [i.sku for i in page.items] == ["RZ-FOOT-102"]


@pytest.mark.asyncio
async def test_a_partial_word_still_finds_the_product(db):
    """"buds" is a substring of "Ecobuds", not a word of its own — whole-word
    matching alone leaves the buyer with nothing."""
    await _seed(
        id="RZ-ELEC-119", title="Ecobuds X", category="electronics", brand="Kestra",
        price_paise=656400, search_text="ecobuds x kestra electronics black",
        attributes={"colour": "black", "tags": ["electronics"]},
    )

    page = await catalog_service.search(q="air buds")

    assert [i.sku for i in page.items] == ["RZ-ELEC-119"]


@pytest.mark.asyncio
async def test_a_query_matching_nothing_still_returns_nothing(db):
    """The looser passes must not turn every miss into a false positive."""
    await _seed(id="RZ-FOOT-102")

    page = await catalog_service.search(q="zzzz nonsense qqqq")

    assert page.items == []
