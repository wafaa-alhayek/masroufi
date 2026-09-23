from datetime import date

import pytest


MONDAY = "2026-07-06"


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d for d in client.get("/api/kitchen/dishes").json()}


@pytest.fixture
def planned_week(client, dishes):
    """A week of lunches and breakfasts, rebuilt for each test that needs it."""
    for existing in client.get(f"/api/kitchen/plan?week_start={MONDAY}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")

    client.post(
        "/api/kitchen/plan",
        json={"plan_date": MONDAY, "slot": "lunch", "dish_id": dishes["maqluba_chicken"]["id"]},
    )
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": "2026-07-08", "slot": "lunch", "dish_id": dishes["mujaddara"]["id"]},
    )
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": MONDAY, "slot": "breakfast", "dish_id": dishes["ful"]["id"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={MONDAY}")
    return client.get(f"/api/kitchen/shopping-list?week_start={MONDAY}").json()


def test_catalogue_and_dishes_are_seeded(client, dishes):
    assert "maqluba_chicken" in dishes
    items = client.get("/api/kitchen/items").json()
    slugs = {i["slug"] for i in items}
    assert {"rice", "chicken", "molokhia_dry", "zaatar"} <= slugs


def test_household_defaults_and_updates(client):
    assert client.get("/api/kitchen/household").json()["adults"] == 2

    body = client.patch(
        "/api/kitchen/household", json={"adults": 2, "children": 3}
    ).json()
    assert body["adult_equivalents"] == pytest.approx(3.8)


def test_rejects_a_negative_household(client):
    assert client.patch("/api/kitchen/household", json={"adults": -1}).status_code == 422


def test_planning_the_same_slot_twice_replaces_it(client, dishes):
    a = client.post(
        "/api/kitchen/plan",
        json={"plan_date": "2026-09-07", "slot": "lunch", "dish_id": dishes["molokhia"]["id"]},
    ).json()
    b = client.post(
        "/api/kitchen/plan",
        json={"plan_date": "2026-09-07", "slot": "lunch", "dish_id": dishes["fasolia"]["id"]},
    ).json()
    assert a["id"] == b["id"]
    assert b["dish_name"] == "White bean stew"
    client.delete(f"/api/kitchen/plan/{b['id']}")


def test_unknown_dish_is_rejected(client):
    resp = client.post(
        "/api/kitchen/plan",
        json={"plan_date": MONDAY, "slot": "lunch", "dish_id": 999999},
    )
    assert resp.status_code == 404


def test_list_is_split_into_daily_and_weekly(planned_week):
    assert planned_week["daily"], "perishables must be listed per day"
    assert planned_week["weekly"], "staples must be aggregated"

    daily_items = {
        line["item_name_en"] for lines in planned_week["daily"].values() for line in lines
    }
    weekly_items = {line["item_name_en"] for line in planned_week["weekly"]}

    assert "Chicken" in daily_items, "chicken cannot be stored without refrigeration"
    assert "Bread" in daily_items
    assert "Rice" in weekly_items, "rice keeps, so buy it once"
    assert not daily_items & weekly_items, "nothing should appear on both lists"


def test_daily_lines_are_keyed_by_the_day_they_are_needed(planned_week):
    assert MONDAY in planned_week["daily"]
    chicken_days = [
        day
        for day, lines in planned_week["daily"].items()
        if any(line["item_name_en"] == "Chicken" for line in lines)
    ]
    assert chicken_days == [MONDAY], "only Monday's lunch uses chicken"


def test_lines_say_which_dish_needs_them(planned_week):
    rice = next(line for line in planned_week["weekly"] if line["item_name_en"] == "Rice")
    assert rice["for_dishes"], "the household should see why an item is on the list"


def _any_pending(week: dict, prefer: str | None = None) -> dict:
    """Pick a line that is still pending.

    Chosen dynamically rather than by name because earlier tests in this module
    buy things, and a purchased line correctly drops off the pending list.
    """
    weekly = week["weekly"]
    assert weekly, "expected at least one pending weekly line"
    if prefer:
        match = next((line for line in weekly if line["item_name_en"] == prefer), None)
        if match:
            return match
    return weekly[0]


def test_confirming_a_purchase_creates_a_transaction(client, planned_week):
    line = _any_pending(planned_week, prefer="Rice")

    before = len(client.get("/api/transactions?limit=1000").json())
    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase",
        json={"paid": 9.5, "source": "jawwalpay", "vendor_note": "سوبر ماركت الأمل"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["unit_price"] == pytest.approx(9.5 / line["quantity"], rel=1e-4)
    if line["unit"] in {"g", "ml"}:
        # How shops quote, and how the WFP and PCBS price datasets are published.
        assert body["price_per_1000"] == pytest.approx(
            9.5 / line["quantity"] * 1000, rel=1e-3
        )
    else:
        assert body["price_per_1000"] is None

    rows = client.get("/api/transactions?limit=1000").json()
    assert len(rows) == before + 1
    created = next(t for t in rows if t["id"] == body["transaction_id"])
    assert created["note"] == "سوبر ماركت الأمل"
    assert created["amount"] == pytest.approx(-9.5)
    assert created["category"] == "groceries"
    assert created["category_source"] == "user"
    assert created["needs_review"] is False


def test_a_purchased_line_cannot_be_bought_twice(client, planned_week):
    line = _any_pending(planned_week)
    first = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 5.0}
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 5.0}
    )
    assert second.status_code == 409


def test_purchase_with_an_unknown_source_is_rejected(client, planned_week):
    line = _any_pending(planned_week)
    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase",
        json={"paid": 5.0, "source": "notawallet"},
    )
    assert resp.status_code == 422


def test_substitutes_are_offered_for_an_unavailable_item(client, planned_week):
    lentil_or_bean = next(
        (
            line
            for line in planned_week["weekly"]
            if line["item_name_en"] in {"Lentils", "Fava beans", "Rice"}
        ),
        None,
    )
    assert lentil_or_bean is not None

    options = client.get(
        f"/api/kitchen/shopping-list/{lentil_or_bean['id']}/substitutes"
    ).json()
    assert options
    assert all(o["slug"] != lentil_or_bean["item_id"] for o in options)


def test_marking_unavailable_with_a_substitute_creates_a_replacement(client, planned_week):
    line = next(
        line
        for line in planned_week["weekly"]
        if client.get(f"/api/kitchen/shopping-list/{line['id']}/substitutes").json()
    )
    options = client.get(f"/api/kitchen/shopping-list/{line['id']}/substitutes").json()

    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/unavailable",
        json={"substitute_item_id": options[0]["id"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "unavailable"
    assert body["replacement_line_id"] is not None

    refreshed = client.get(
        f"/api/kitchen/shopping-list?week_start={MONDAY}&include_settled=true"
    ).json()
    states = {
        line["id"]: line["state"]
        for group in [refreshed["weekly"], *refreshed["daily"].values()]
        for line in group
    }
    assert states[line["id"]] == "unavailable"
    assert states[body["replacement_line_id"]] == "pending"


def test_marking_unavailable_without_a_substitute_still_records_it(client, planned_week):
    line = _any_pending(planned_week)
    resp = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/unavailable", json={}
    )
    assert resp.status_code == 200
    assert resp.json()["replacement_line_id"] is None


def test_regenerating_keeps_a_shop_already_done(client, planned_week, dishes):
    line = _any_pending(planned_week)
    client.post(f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 9.0})

    client.post(
        "/api/kitchen/plan",
        json={"plan_date": "2026-07-10", "slot": "lunch", "dish_id": dishes["molokhia"]["id"]},
    )
    result = client.post(f"/api/kitchen/shopping-list?week_start={MONDAY}").json()
    assert result["lines_kept"] >= 1

    settled = client.get(
        f"/api/kitchen/shopping-list?week_start={MONDAY}&include_settled=true"
    ).json()
    purchased = [
        line
        for group in [settled["weekly"], *settled["daily"].values()]
        for line in group
        if line["state"] == "purchased"
    ]
    assert purchased, "what was already bought must survive regeneration"


def test_household_can_correct_a_dish(client, dishes):
    dish_id = dishes["lentil_soup"]["id"]
    before = client.get(f"/api/kitchen/dishes/{dish_id}/ingredients").json()
    assert before

    items = {i["slug"]: i for i in client.get("/api/kitchen/items").json()}
    resp = client.put(
        f"/api/kitchen/dishes/{dish_id}/ingredients",
        json={
            "ingredients": [
                {"item_id": items["lentils"]["id"], "qty_per_adult": 90},
                {"item_id": items["onion"]["id"], "qty_per_adult": 50},
                {"item_id": items["cumin"]["id"], "qty_per_adult": 3, "optional": True},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert {row["item_slug"] for row in after} == {"lentils", "onion", "cumin"}
    assert next(r for r in after if r["item_slug"] == "lentils")["qty_per_adult"] == 90
    assert next(r for r in after if r["item_slug"] == "cumin")["optional"] is True

    updated = next(
        d for d in client.get("/api/kitchen/dishes").json() if d["id"] == dish_id
    )
    assert updated["needs_review"] is False


def test_dish_edit_rejects_bad_input(client, dishes):
    dish_id = dishes["lentil_soup"]["id"]
    items = {i["slug"]: i for i in client.get("/api/kitchen/items").json()}

    assert client.put(
        f"/api/kitchen/dishes/{dish_id}/ingredients", json={"ingredients": []}
    ).status_code == 422

    assert client.put(
        f"/api/kitchen/dishes/{dish_id}/ingredients",
        json={"ingredients": [{"item_id": 999999, "qty_per_adult": 10}]},
    ).status_code == 422

    duplicate = client.put(
        f"/api/kitchen/dishes/{dish_id}/ingredients",
        json={
            "ingredients": [
                {"item_id": items["rice"]["id"], "qty_per_adult": 80},
                {"item_id": items["rice"]["id"], "qty_per_adult": 40},
            ]
        },
    )
    assert duplicate.status_code == 422
    assert "twice" in duplicate.json()["detail"]


def test_changing_shelf_life_moves_an_item_between_the_lists(client, dishes):
    """The consequential edit: what keeps and what does not is local knowledge."""
    week = "2026-08-03"
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week, "slot": "lunch", "dish_id": dishes["mujaddara"]["id"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    assert any(line["item_name_en"] == "Rice" for line in listed["weekly"])

    items = {i["slug"]: i for i in client.get("/api/kitchen/items").json()}
    rice_id = items["rice"]["id"]
    assert (
        client.patch(f"/api/kitchen/items/{rice_id}", json={"shelf_life": "perishable"}).status_code
        == 200
    )

    client.post(f"/api/kitchen/shopping-list?week_start={week}")
    moved = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    daily_names = {line["item_name_en"] for lines in moved["daily"].values() for line in lines}
    assert "Rice" in daily_names
    assert not any(line["item_name_en"] == "Rice" for line in moved["weekly"])

    # Put it back, so later tests see the seeded catalogue.
    client.patch(f"/api/kitchen/items/{rice_id}", json={"shelf_life": "stable"})


def test_unknown_item_patch_is_404(client):
    assert client.patch("/api/kitchen/items/999999", json={"purchase_step": 10}).status_code == 404


def test_portion_override_endpoint(client, dishes):
    resp = client.put(
        f"/api/kitchen/dishes/{dishes['mujaddara']['id']}/portion", json={"factor": 1.5}
    )
    assert resp.status_code == 200
    assert resp.json()["factor"] == 1.5

    assert (
        client.put("/api/kitchen/dishes/999999/portion", json={"factor": 1.5}).status_code == 404
    )
    assert (
        client.put(
            f"/api/kitchen/dishes/{dishes['mujaddara']['id']}/portion", json={"factor": 0}
        ).status_code
        == 422
    )
