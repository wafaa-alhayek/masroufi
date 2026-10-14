"""The researched catalogue in data/raw, as it reaches the item table."""

import csv
from collections import Counter

from app.kitchen.catalogue import CATALOGUE, RAW_DIR, canonical_slug, raw_items, seed_items
from app.models import ItemRole
from app.services.shopping import _SUBSTITUTABLE


def _raw_rows(kind: str) -> list[dict]:
    rows = []
    for path in sorted(RAW_DIR.glob(f"*_{kind}.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def test_every_slug_is_seeded_once():
    slugs = Counter(i.slug for i in seed_items())
    assert [s for s, n in slugs.items() if n > 1] == []
    assert {canonical_slug(r["slug"]) for r in _raw_rows("items")} <= set(slugs)


def test_the_hand_written_row_wins_where_both_exist():
    """Research says tinned tuna keeps three years; the tuned catalogue says two."""
    by_slug = {i.slug: i for i in seed_items()}
    tuna = by_slug["canned_tuna"]
    assert tuna.role is ItemRole.FISH
    assert tuna.keeps_days_at_20c == 720
    assert "canned_tuna" not in {i.slug for i in raw_items()}


def test_one_thing_on_the_shelf_is_one_item():
    """Corned beef and tinned meat share an Arabic name: they are the same tin."""
    slugs = {i.slug for i in seed_items()}
    assert {"canned_meat", "canned_tomato", "dates"} <= slugs
    assert not {"canned_corned_beef", "canned_tomatoes", "dates_dried"} & slugs


def test_low_confidence_research_waits_for_review():
    low = {canonical_slug(r["slug"]) for r in _raw_rows("items") if r["confidence"] == "low"}
    hand = {row[0] for row in CATALOGUE}
    flagged = {i.slug for i in raw_items() if i.needs_review}
    assert flagged == low - hand
    assert flagged, "the research marks some rows low; none reaching review means the flag is lost"


def test_every_product_names_an_item_that_exists():
    """A brand row pointing at nothing would be unmatchable on a receipt."""
    slugs = {i.slug for i in seed_items()}
    named = {r["item_slug"] for r in _raw_rows("products")}
    assert {s for s in named if canonical_slug(s) not in slugs} == set()


def test_baby_food_is_never_offered_as_a_substitute():
    """Formula is chosen for a child; the app does not swap it on its own."""
    assert any(i.role is ItemRole.BABY for i in seed_items())
    assert ItemRole.BABY not in _SUBSTITUTABLE
    assert ItemRole.READY_MEAL not in _SUBSTITUTABLE
