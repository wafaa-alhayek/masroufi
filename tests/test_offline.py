"""The offline path: one bundle down, a replayable queue up.

The replay tests matter more than they look. A queued purchase replayed without
being recognised as a replay records the spend twice, which means the app lies
about what a household spent — the single worst thing this app could do.
"""

from datetime import date, timedelta

import pytest

WEEK = date(2028, 6, 5)


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d["id"] for d in client.get("/api/kitchen/dishes").json()}


@pytest.fixture(scope="module")
def items(client):
    return {i["slug"]: i for i in client.get("/api/kitchen/items").json()}


@pytest.fixture
def planned(client, dishes):
    for existing in client.get(f"/api/kitchen/plan?week_start={WEEK}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    for offset, slug in enumerate(["mujaddara", "molokhia", "fasolia"]):
        client.post(
            "/api/kitchen/plan",
            json={
                "plan_date": (WEEK + timedelta(days=offset)).isoformat(),
                "slot": "lunch",
                "dish_id": dishes[slug],
            },
        )
    client.post(f"/api/kitchen/shopping-list?week_start={WEEK}")
    return client.get(f"/api/kitchen/shopping-list?week_start={WEEK}").json()


# --- the bundle ---------------------------------------------------------------


def test_the_bundle_carries_everything_needed_to_work_offline(client, planned):
    b = client.get(f"/api/bootstrap?week_start={WEEK}").json()

    assert b["bundle_version"] >= 1
    assert b["week_start"] == WEEK.isoformat()
    for key in (
        "items",
        "dishes",
        "household",
        "sources",
        "categories",
        "rules",
        "climate_normals_c",
    ):
        assert b[key], f"{key} missing from the bundle"

    assert len(b["items"]) >= 40
    assert len(b["dishes"]) >= 12
    assert b["plan"], "the week's plan travels with it"
    assert b["lines"], "so does the list already generated for it"


def test_shelf_life_ships_evaluated_not_as_a_model(client):
    """The client does one comparison, not physics.

    Porting the Q10 curve to TypeScript would mean two implementations of a rule
    this app's correctness rests on, and they would drift.
    """
    b = client.get(f"/api/bootstrap?week_start={WEEK}").json()
    bread = next(i for i in b["items"] if i["slug"] == "bread")

    assert len(bread["keeps_days_by_day"]) == 7
    assert all(v > 0 for v in bread["keeps_days_by_day"].values())
    # And the inputs come too, so a client can recompute for a week it has no
    # forecast for using the monthly normals.
    assert bread["keeps_days_at_20c"] > 0
    assert bread["q10"] > 1


def test_the_rules_the_client_needs_are_stated_not_guessed(client):
    rules = client.get("/api/bootstrap").json()["rules"]
    assert rules["daily_threshold_days"] > 0
    assert rules["reference_c"] == 20.0
    assert rules["burner_kg_per_hour"] > 0
    assert rules["price_stale_after_days"] > 0


def test_climate_normals_let_a_forecastless_week_still_split(client):
    normals = client.get("/api/bootstrap").json()["climate_normals_c"]
    assert len(normals) == 12
    assert max(normals.values()) > min(normals.values())


def test_the_bundle_says_where_its_temperatures_came_from(client):
    b = client.get(f"/api/bootstrap?week_start={WEEK}").json()
    assert b["weather_source"] in {"open-meteo", "climate-normals"}
    assert set(b["temperatures"]) == set(b["temperatures_estimated"])
    assert all(b["temperatures_estimated"].values()), "weather is off in tests"


def test_gas_per_dish_is_precomputed(client):
    dishes = client.get("/api/bootstrap").json()["dishes"]
    zaatar = next(d for d in dishes if d["slug"] == "zaatar_bread")
    beans = next(d for d in dishes if d["slug"] == "fasolia")
    assert zaatar["gas_kg"] == 0.0
    assert beans["gas_kg"] > zaatar["gas_kg"]


def test_spoiled_stock_is_left_out_of_the_bundle(client, items):
    client.post(
        "/api/pantry/stock",
        json={"item_id": items["bulgur"]["id"], "quantity": 900, "source": "aid"},
    )
    stock = client.get("/api/pantry/stock").json()["items"]
    row = next(r for r in stock if r["name_en"] == "Bulgur")
    client.patch(f"/api/pantry/stock/{row['id']}/condition", json={"condition": "spoiled"})

    bundled = client.get("/api/bootstrap").json()["stock"]
    assert items["bulgur"]["id"] not in {s["item_id"] for s in bundled}

    client.patch(f"/api/pantry/stock/{row['id']}/condition", json={"condition": "good"})


def test_weary_items_travel_so_the_client_can_honour_them_offline(client, items):
    client.put(
        f"/api/pantry/items/{items['lentils']['id']}/feeling", json={"feeling": "weary"}
    )
    b = client.get("/api/bootstrap").json()
    assert items["lentils"]["id"] in b["weary_item_ids"]
    client.put(
        f"/api/pantry/items/{items['lentils']['id']}/feeling", json={"feeling": "neutral"}
    )


# --- the queue replaying ------------------------------------------------------


def test_a_replayed_purchase_is_not_recorded_twice(client, planned):
    """The worst failure this app could have, so it is pinned directly."""
    line = planned["weekly"][0]
    key = "tap-0001"
    payload = {"paid": 7.25, "key": key}

    first = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase", json=payload
    )
    assert first.status_code == 200, first.text

    before = len(client.get("/api/transactions?limit=1000").json())
    second = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase", json=payload
    )

    assert second.status_code == 200, "a replay succeeds rather than erroring"
    assert second.json() == first.json(), "and returns the original answer"
    assert len(client.get("/api/transactions?limit=1000").json()) == before


def test_without_a_key_a_second_purchase_is_refused_not_duplicated(client, planned):
    """A client that forgets to send a key still cannot double-record."""
    line = planned["weekly"][0]
    assert (
        client.post(
            f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 5.0}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/kitchen/shopping-list/{line['id']}/purchase", json={"paid": 5.0}
        ).status_code
        == 409
    )


def test_different_keys_are_different_actions(client, planned):
    a, b = planned["weekly"][0], planned["weekly"][1]
    first = client.post(
        f"/api/kitchen/shopping-list/{a['id']}/purchase",
        json={"paid": 3.0, "key": "tap-a"},
    )
    second = client.post(
        f"/api/kitchen/shopping-list/{b['id']}/purchase",
        json={"paid": 4.0, "key": "tap-b"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["transaction_id"] != second.json()["transaction_id"]


def test_a_replayed_parcel_is_recorded_once(client, items):
    payload = {
        "received_on": "2028-06-01",
        "label": "queued offline",
        "key": "tap-parcel-1",
        "lines": [{"item_id": items["freekeh"]["id"], "quantity": 3000}],
    }
    first = client.post("/api/pantry/parcel", json=payload).json()
    held_after_first = sum(
        row["quantity"]
        for row in client.get("/api/pantry/stock").json()["items"]
        if row["name_en"] == "Freekeh"
    )

    second = client.post("/api/pantry/parcel", json=payload).json()
    held_after_second = sum(
        row["quantity"]
        for row in client.get("/api/pantry/stock").json()["items"]
        if row["name_en"] == "Freekeh"
    )

    assert second == first
    assert held_after_second == held_after_first, "the cupboard did not double"


def test_a_replayed_from_stock_tick_does_not_double_deduct(client, items, dishes):
    week = date(2028, 7, 3)
    client.post(
        "/api/pantry/stock",
        # Deliberately only part of what the dish needs, so a line survives to tick.
        json={"item_id": items["courgette"]["id"], "quantity": 100, "source": "purchased"},
    )
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["koosa_mahshi"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    lines = [line for group in [listed["weekly"], *listed["daily"].values()] for line in group]
    target = next((line for line in lines if line["item_name_en"] == "Courgette"), None)
    assert target is not None, "expected a courgette line still to buy"

    payload = {"deduct": True, "key": "tap-stock-1"}
    first = client.post(
        f"/api/kitchen/shopping-list/{target['id']}/from-stock", json=payload
    ).json()
    after_first = _held(client, "Courgette")

    second = client.post(
        f"/api/kitchen/shopping-list/{target['id']}/from-stock", json=payload
    ).json()

    assert second == first
    assert _held(client, "Courgette") == after_first


def test_a_replayed_unavailable_mark_is_idempotent(client, planned):
    line = next(
        line
        for line in planned["weekly"]
        if client.get(f"/api/kitchen/shopping-list/{line['id']}/substitutes").json()
    )
    options = client.get(f"/api/kitchen/shopping-list/{line['id']}/substitutes").json()
    payload = {"substitute_item_id": options[0]["id"], "key": "tap-gap-1"}

    first = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/unavailable", json=payload
    ).json()
    second = client.post(
        f"/api/kitchen/shopping-list/{line['id']}/unavailable", json=payload
    ).json()

    assert second == first
    assert first["replacement_line_id"] == second["replacement_line_id"], (
        "a replay must not create a second substitute line"
    )


def _held(client, name: str) -> float:
    return sum(
        row["quantity"]
        for row in client.get("/api/pantry/stock").json()["items"]
        if row["name_en"] == name
    )


# --- what the list now tells the UI ------------------------------------------


def test_the_list_reports_the_temperature_it_split_on(client, planned):
    assert "weather_source" in planned
    assert isinstance(planned["temperatures"], list)


def test_lines_report_what_the_cupboard_covered(client, items, dishes):
    week = date(2028, 8, 7)
    client.post(
        "/api/pantry/stock",
        json={"item_id": items["lentils"]["id"], "quantity": 4000, "source": "aid"},
    )
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["mujaddara"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(
        f"/api/kitchen/shopping-list?week_start={week}&include_settled=true"
    ).json()
    lines = {
        line["item_name_en"]: line
        for group in [listed["weekly"], *listed["daily"].values()]
        for line in group
    }

    lentils = lines["Lentils"]
    assert lentils["state"] == "from_stock", "the app knew, and can show that it knew"
    assert lentils["from_stock"] > 0
    assert lentils["required"] > 0

    onions = lines["Onions"]
    assert onions["state"] == "pending"
    assert onions["from_stock"] == 0
    assert onions["required"] > 0
