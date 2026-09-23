"""The four things the designer's note asked the backend for.

Parcel templates, tinned goods, the dish library's review gate and search, and the
three home-screen numbers.
"""

from datetime import date, timedelta

import pytest

WEEK = date(2029, 2, 5)


@pytest.fixture(scope="module")
def items(client):
    return {i["slug"]: i for i in client.get("/api/kitchen/items").json()}


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d["id"] for d in client.get("/api/kitchen/dishes").json()}


# --- 1. the parcel sheet ------------------------------------------------------


def test_a_new_household_gets_a_plausible_parcel_to_correct(client):
    """An empty form is the version nobody fills in."""
    body = client.get("/api/pantry/parcel/template").json()
    assert body["source"] == "default"
    slugs = {line["slug"] for line in body["lines"]}
    assert {"flour", "rice", "lentils", "veg_oil"} <= slugs
    assert all(line["quantity"] > 0 for line in body["lines"])
    assert all(line["unit"] for line in body["lines"])


def test_the_next_parcel_prefills_from_the_last_one(client, items):
    """Including the household's corrections, since the template is built from what
    they actually recorded rather than from the default."""
    client.post(
        "/api/pantry/parcel",
        json={
            "received_on": "2029-01-10",
            "label": "UNRWA",
            "lines": [
                {"item_id": items["flour"]["id"], "quantity": 12000},
                {"item_id": items["canned_tuna"]["id"], "quantity": 900},
            ],
        },
    )

    body = client.get("/api/pantry/parcel/template").json()
    assert body["source"] == "last_parcel"
    by_slug = {line["slug"]: line for line in body["lines"]}
    assert set(by_slug) == {"flour", "canned_tuna"}
    assert by_slug["flour"]["quantity"] == 12000, "their correction carried forward"


def test_the_template_travels_in_the_bundle(client):
    bundle = client.get("/api/bootstrap").json()
    assert bundle["parcel_template"]["lines"], "recording a parcel must work offline"
    assert bundle["parcel_template"]["source"] in {"default", "last_parcel"}


def test_stock_the_household_already_had_has_its_own_source(client, items):
    """Olive oil that was in the house before the app existed is not "unknown"."""
    resp = client.post(
        "/api/pantry/stock",
        json={
            "item_id": items["olive_oil"]["id"],
            "quantity": 700,
            "source": "already_had",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "already_had"


# --- 2. tinned and parcel goods ----------------------------------------------


def test_the_catalogue_covers_what_is_actually_in_a_parcel(client, items):
    for slug in (
        "canned_tuna",
        "canned_sardines",
        "canned_meat",
        "canned_fava",
        "canned_chickpeas",
        "canned_white_beans",
        "canned_tomato",
        "milk_powder",
        "halawa",
        "dates",
    ):
        assert slug in items, f"{slug} missing — a parcel cannot be recorded without it"


def test_tinned_food_keeps_although_its_role_is_perishable(client, items):
    """Role and shelf life are independent, and tins are where that shows."""
    tuna = items["canned_tuna"]
    assert tuna["role"] == "fish"
    assert tuna["shelf_life"] == "stable"
    assert tuna["keeps_days_at_20c"] > 365

    powder = items["milk_powder"]
    assert powder["role"] == "dairy"
    assert powder["shelf_life"] == "stable"


def test_a_tin_can_stand_in_for_the_fresh_thing(client, dishes):
    """Same role, so the substitution logic already handles it."""
    week = date(2029, 3, 5)
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["fasolia"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    beans = next(
        line
        for group in [listed["weekly"], *listed["daily"].values()]
        for line in group
        if line["item_name_en"] == "White beans"
    )
    options = {
        o["slug"]
        for o in client.get(
            f"/api/kitchen/shopping-list/{beans['id']}/substitutes"
        ).json()
    }
    assert "canned_white_beans" in options or "canned_fava" in options


def test_purchase_steps_match_how_tins_are_sold(client, items):
    assert items["canned_tuna"]["purchase_step"] == 150
    assert items["canned_fava"]["purchase_step"] == 400


# --- 3. the dish library -----------------------------------------------------


def test_an_unreviewed_dish_is_not_offered(client, dishes, items):
    """A wrong amount is wasted money or a short meal, so nothing unchecked is used."""
    listed = {d["slug"] for d in client.get("/api/kitchen/dishes").json()}
    assert listed, "seeded dishes are reviewed"

    with_unreviewed = client.get(
        "/api/kitchen/dishes?include_unreviewed=true"
    ).json()
    assert len(with_unreviewed) >= len(listed)
    assert all(not d["needs_review"] for d in client.get("/api/kitchen/dishes").json())


def test_dishes_carry_their_drafting_confidence_for_a_reviewer(client):
    for dish in client.get("/api/kitchen/dishes?include_unreviewed=true").json():
        assert dish["draft_confidence"] in {"high", "medium", "low"}
        assert "in_offline_bundle" in dish


def test_search_matches_arabic_and_english(client):
    assert client.get("/api/kitchen/dishes?q=ملوخية").json()
    assert client.get("/api/kitchen/dishes?q=Maqluba").json()
    assert client.get("/api/kitchen/dishes?q=mujaddara").json()
    assert client.get("/api/kitchen/dishes?q=zzzznothing").json() == []


def test_search_is_case_and_partial_tolerant(client):
    assert client.get("/api/kitchen/dishes?q=LENTIL").json()
    assert client.get("/api/kitchen/dishes?q=shak").json()


def test_the_offline_set_can_be_asked_for_separately(client):
    offline = client.get("/api/kitchen/dishes?offline_only=true").json()
    everything = client.get("/api/kitchen/dishes").json()
    assert offline
    assert len(offline) <= len(everything)


def test_the_bundle_ships_only_the_offline_set(client):
    bundle = client.get("/api/bootstrap").json()
    assert bundle["dishes_offline_only"] is True
    assert all("aliases_ar" in d for d in bundle["dishes"])


# --- 4. the home screen ------------------------------------------------------


def test_the_three_home_numbers(client, items, dishes):
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

    body = client.get(f"/api/home?week_start={WEEK}").json()

    assert body["week_start"] == WEEK.isoformat()
    assert "spent" in body["spend"]
    assert isinstance(body["runway"], list)
    assert isinstance(body["at_risk"], list)


def test_an_estimate_that_cannot_cover_the_list_says_so(client):
    spend = client.get(f"/api/home?week_start={WEEK}").json()["spend"]
    if spend["estimate_missing"]:
        assert spend["caveat"]
        assert "higher" in spend["caveat"]


def test_runway_reports_weeks_left_at_the_plan_rate(client, items):
    client.post(
        "/api/pantry/parcel",
        json={
            "received_on": WEEK.isoformat(),
            "lines": [
                {"item_id": items["rice"]["id"], "quantity": 9000},
                {"item_id": items["lentils"]["id"], "quantity": 1000},
            ],
        },
    )
    runway = client.get(f"/api/home?week_start={WEEK}").json()["runway"]
    by_name = {r["name_en"]: r for r in runway}

    assert "Rice" in by_name, "rice is used by this plan and is in the cupboard"
    rice = by_name["Rice"]
    assert rice["per_week"] > 0
    assert rice["weeks_left"] == pytest.approx(rice["quantity"] / rice["per_week"], abs=0.1)

    assert runway == sorted(runway, key=lambda r: r["weeks_left"]), "shortest first"


def test_an_item_the_plan_does_not_use_has_no_runway(client, items):
    """There is no honest rate for it, so nothing is claimed."""
    client.post(
        "/api/pantry/stock",
        json={"item_id": items["coffee"]["id"], "quantity": 500, "source": "purchased"},
    )
    runway = client.get(f"/api/home?week_start={WEEK}").json()["runway"]
    assert "Coffee" not in {r["name_en"] for r in runway}


def test_no_plan_means_no_runway_and_a_reason(client):
    body = client.get("/api/home?week_start=2031-01-06").json()
    assert body["runway"] == []
    assert body["runway_note"] is not None
    assert "no rate" in body["runway_note"]


def test_stock_at_risk_reaches_the_home_screen(client, items):
    client.post(
        "/api/pantry/stock",
        json={
            "item_id": items["bread"]["id"],
            "quantity": 800,
            "source": "purchased",
            "acquired_on": (WEEK - timedelta(days=20)).isoformat(),
        },
    )
    at_risk = client.get(f"/api/home?week_start={WEEK}").json()["at_risk"]
    assert any(row["name_en"] == "Bread" for row in at_risk)
    assert all(row["note"] for row in at_risk)


def test_grocery_spend_counts_only_this_week(client, items, dishes):
    week = date(2029, 5, 7)
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["koosa_mahshi"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    before = client.get(f"/api/home?week_start={week}").json()["spend"]["spent"]

    listed = client.get(f"/api/kitchen/shopping-list?week_start={week}").json()
    line = [line for group in [listed["weekly"], *listed["daily"].values()] for line in group][0]
    client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase",
        json={"paid": 11.0, "purchased_on": week.isoformat()},
    )

    after = client.get(f"/api/home?week_start={week}").json()["spend"]["spent"]
    assert after == pytest.approx(before + 11.0)

    # A different week is unaffected.
    other = client.get("/api/home?week_start=2030-05-06").json()["spend"]["spent"]
    assert other == 0
