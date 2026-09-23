from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app

WEEK = date(2027, 4, 5)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def items(client):
    return {i["slug"]: i for i in client.get("/api/kitchen/items").json()}


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d["id"] for d in client.get("/api/kitchen/dishes").json()}


def test_a_parcel_is_recorded_in_one_call(client, items):
    """Aid arrives as a bundle. Eight separate forms means nobody enters any."""
    resp = client.post(
        "/api/pantry/parcel",
        json={
            "received_on": "2027-04-01",
            "label": "UNRWA",
            "lines": [
                {"item_id": items["lentils"]["id"], "quantity": 5000},
                {"item_id": items["rice"]["id"], "quantity": 10000},
                {"item_id": items["flour"]["id"], "quantity": 10000},
                {"item_id": items["veg_oil"]["id"], "quantity": 1500},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items_recorded"] == 4

    stock = client.get("/api/pantry/stock?on=2027-04-05").json()
    held = {row["name_en"]: row for row in stock["items"]}
    assert held["Lentils"]["quantity"] == 5000
    assert held["Lentils"]["source"] == "aid"


def test_an_empty_parcel_is_rejected(client):
    assert client.post("/api/pantry/parcel", json={"lines": []}).status_code == 422


def test_unknown_items_in_a_parcel_are_rejected(client):
    resp = client.post(
        "/api/pantry/parcel", json={"lines": [{"item_id": 999999, "quantity": 100}]}
    )
    assert resp.status_code == 422


def test_the_shopping_list_stops_asking_for_the_aid_staples(client, dishes):
    """The whole point: a household given lentils is not told to buy lentils."""
    for existing in client.get(f"/api/kitchen/plan?week_start={WEEK}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": WEEK.isoformat(), "slot": "lunch", "dish_id": dishes["mujaddara"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={WEEK}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={WEEK}").json()
    names = {line["item_name_en"] for line in listed["weekly"]}
    assert "Lentils" not in names
    assert "Rice" not in names
    assert "Onions" in names, "what is not in the cupboard is still asked for"


def test_the_we_already_have_this_button(client, items, dishes):
    later = date(2027, 5, 3)
    for existing in client.get(f"/api/kitchen/plan?week_start={later}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": later.isoformat(), "slot": "lunch", "dish_id": dishes["koosa_mahshi"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={later}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={later}").json()
    line = next(
        line
        for group in [listed["weekly"], *listed["daily"].values()]
        for line in group
        if line["item_name_en"] == "Tomato paste"
    )

    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/from-stock", json={"deduct": False}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "from_stock"

    after = client.get(f"/api/kitchen/shopping-list?week_start={later}").json()
    remaining = {
        line["item_name_en"]
        for group in [after["weekly"], *after["daily"].values()]
        for line in group
    }
    assert "Tomato paste" not in remaining


def _held(client, name: str) -> float:
    return sum(
        row["quantity"]
        for row in client.get("/api/pantry/stock").json()["items"]
        if row["name_en"] == name
    )


def test_from_stock_deducts_when_the_cupboard_is_tracked(client, items, dishes):
    """Ticking "we already have this" spends the stock, so next week knows too."""
    week = date(2027, 6, 7)
    client.post(
        "/api/pantry/stock",
        json={"item_id": items["courgette"]["id"], "quantity": 4000, "source": "purchased"},
    )
    before = _held(client, "Courgette")
    assert before >= 4000

    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["koosa_mahshi"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    lines = [line for group in [listed["weekly"], *listed["daily"].values()] for line in group]
    courgette = next((line for line in lines if line["item_name_en"] == "Courgette"), None)
    if courgette is None:
        # Already fully covered by the 4 kg just recorded, which is itself the
        # behaviour under test.
        assert _held(client, "Courgette") == before
        return

    resp = client.post(
        f"/api/kitchen/shopping-list/{courgette['id']}/from-stock", json={"deduct": True}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deducted_from_stock"] > 0
    assert _held(client, "Courgette") < before


def test_a_purchased_line_cannot_also_be_taken_from_stock(client, dishes):
    week = date(2027, 7, 5)
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "dinner", "dish_id": dishes["shakshuka"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")
    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    line = [line for group in [listed["weekly"], *listed["daily"].values()] for line in group][0]

    client.post(f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 4.0})
    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/from-stock", json={"deduct": False}
    )
    assert resp.status_code == 409


# --- the flour case ----------------------------------------------------------


def test_long_stored_flour_is_reported_as_a_risk(client, items):
    """Flour given out in spring is not spring flour by autumn."""
    stock = client.get("/api/pantry/stock?on=2027-11-01").json()
    flour = next(row for row in stock["items"] if row["name_en"] == "Wheat flour")

    assert flour["days_held"] > 180
    # 214 days of the ~219 it keeps at a November 23.5 C: not past it, but close
    # enough that the household should use it before anything newer.
    assert flour["fraction_used"] > 0.75
    assert flour["note"] is not None
    assert "before the newer stock" in flour["note"]
    assert any("Wheat flour" in w for w in stock["warnings"])


def test_nothing_is_marked_spoiled_without_the_household_saying_so(client):
    stock = client.get("/api/pantry/stock?on=2027-11-01").json()
    assert all(row["condition"] != "spoiled" for row in stock["items"])


def test_marking_stock_spoiled_makes_it_stop_counting(client, items, dishes):
    week = date(2027, 8, 2)
    stock = client.get("/api/pantry/stock").json()
    lentils = next(row for row in stock["items"] if row["name_en"] == "Lentils")

    resp = client.patch(
        f"/api/pantry/stock/{lentils['id']}/condition", json={"condition": "spoiled"}
    )
    assert resp.status_code == 200

    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["mujaddara"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    names = {line["item_name_en"] for line in listed["weekly"]}
    assert "Lentils" in names, "spoiled stock must not suppress a needed purchase"

    # Put it back so later tests see a stocked cupboard.
    client.patch(
        f"/api/pantry/stock/{lentils['id']}/condition", json={"condition": "good"}
    )


def test_unknown_stock_is_404(client):
    resp = client.patch("/api/pantry/stock/999999/condition", json={"condition": "good"})
    assert resp.status_code == 404


# --- weariness and suggestions ------------------------------------------------


def test_marking_weariness_needs_no_reason(client, items):
    resp = client.put(
        f"/api/pantry/items/{items['lentils']['id']}/feeling", json={"feeling": "weary"}
    )
    assert resp.status_code == 200
    assert resp.json()["feeling"] == "weary"

    listed = client.get("/api/pantry/items/feelings").json()
    assert any(row["name_en"] == "Lentils" for row in listed)


def test_weary_dishes_rank_below_others(client):
    ranked = client.get("/api/pantry/suggestions?limit=50").json()
    by_name = {s["name_en"]: s for s in ranked}

    mujaddara = by_name["Mujaddara"]
    assert "Lentils" in mujaddara["weary_items"]
    assert "enough of" in mujaddara["why"]

    # Every dish they are not tired of ranks above every dish they are — not
    # merely most of them.
    weary_positions = [i for i, s in enumerate(ranked) if s["weary_items"]]
    fresh_positions = [i for i, s in enumerate(ranked) if not s["weary_items"]]
    assert weary_positions and fresh_positions
    assert min(weary_positions) > max(fresh_positions)


def test_suggestions_never_nudge_back(client):
    for s in client.get("/api/pantry/suggestions?limit=50").json():
        lowered = s["why"].lower()
        for nudge in ("should", "cheap", "healthy", "try to", "affordable"):
            assert nudge not in lowered


def test_suggestions_report_what_is_missing_and_why(client):
    ranked = client.get("/api/pantry/suggestions?limit=50").json()
    assert ranked
    top = ranked[0]
    assert 0.0 <= top["stock_cover"] <= 1.0
    assert top["why"]
    assert isinstance(top["missing"], list)


def test_a_flavour_only_dish_is_flagged_when_one_exists(client, items):
    """The honest answer to lentil fatigue: a different meal for a few shekels."""
    client.put(
        f"/api/pantry/items/{items['lentils']['id']}/feeling", json={"feeling": "neutral"}
    )
    for slug, qty in (
        ("carrot", 3000),
        ("potato", 4000),
        ("onion", 3000),
        ("salt", 500),
    ):
        client.post(
            "/api/pantry/stock",
            json={"item_id": items[slug]["id"], "quantity": qty, "source": "purchased"},
        )

    ranked = client.get("/api/pantry/suggestions?limit=50").json()
    stew = next(s for s in ranked if s["name_en"] == "Lentil and vegetable stew")
    assert stew["flavour_only"], stew["missing"]
    assert {m["name_en"] for m in stew["missing"]} == {"Tomato paste"}
    assert "price of a spice" in stew["why"]


def test_filtering_suggestions_by_slot(client):
    breakfast = client.get("/api/pantry/suggestions?slot=breakfast&limit=50").json()
    assert breakfast
    assert all(s["slot"] == "breakfast" for s in breakfast)


def test_unknown_item_feeling_is_404(client):
    resp = client.put("/api/pantry/items/999999/feeling", json={"feeling": "weary"})
    assert resp.status_code == 404
