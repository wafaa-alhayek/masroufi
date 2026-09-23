from datetime import date, timedelta

import pytest


WEEK = date(2028, 3, 6)


@pytest.fixture(scope="module")
def items(client):
    return {i["slug"]: i for i in client.get("/api/kitchen/items").json()}


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d["id"] for d in client.get("/api/kitchen/dishes").json()}


def _today() -> date:
    return date.today()


def test_an_unknown_price_is_a_404_not_a_guess(client, items):
    resp = client.get(f"/api/prices/estimate/{items['freekeh']['id']}")
    assert resp.status_code == 404
    assert "rather than relying on a guess" in resp.json()["detail"]


def test_recording_and_reading_a_price(client, items):
    resp = client.post(
        "/api/prices/observations",
        json={
            "item_id": items["rice"]["id"],
            "paid": 6.20,
            "quantity": 1000,
            "source": "manual",
            "observed_on": _today().isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text

    est = client.get(f"/api/prices/estimate/{items['rice']['id']}").json()
    assert est["price"] == 6.20
    assert est["quoted_per"] == 1000
    assert est["stale"] is False
    assert est["sources"] == ["manual"]


def test_a_purchase_records_a_price_by_itself(client, items, dishes):
    """The household never types this one in; buying something is the observation."""
    for existing in client.get(f"/api/kitchen/plan?week_start={WEEK}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": WEEK.isoformat(), "slot": "lunch", "dish_id": dishes["koosa_mahshi"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={WEEK}")

    listed = client.get(f"/api/kitchen/shopping-list?week_start={WEEK}").json()
    line = next(
        line
        for group in [listed["weekly"], *listed["daily"].values()]
        for line in group
        if line["item_name_en"] == "Courgette"
    )
    client.post(
        f"/api/kitchen/shopping-list/{line['id']}/purchase",
        json={"paid": 9.0, "purchased_on": _today().isoformat()},
    )

    est = client.get(f"/api/prices/estimate/{items['courgette']['id']}").json()
    assert est["observations"] >= 1
    assert "purchase" in est["sources"]


def test_the_observation_history_can_be_audited(client, items):
    for paid, days in ((6.0, 0), (7.5, 4)):
        client.post(
            "/api/prices/observations",
            json={
                "item_id": items["lentils"]["id"],
                "paid": paid,
                "quantity": 1000,
                "source": "manual",
                "observed_on": (_today() - timedelta(days=days)).isoformat(),
            },
        )

    history = client.get(f"/api/prices/observations/{items['lentils']['id']}").json()
    assert len(history) >= 2
    assert history[0]["observed_on"] >= history[-1]["observed_on"], "newest first"
    assert all(row["paid"] > 0 and row["quantity"] > 0 for row in history)


def test_a_correction_moves_the_estimate_without_deleting_anything(client, items):
    before = client.get(f"/api/prices/estimate/{items['lentils']['id']}").json()
    history_before = len(
        client.get(f"/api/prices/observations/{items['lentils']['id']}").json()
    )

    client.post(
        "/api/prices/observations",
        json={
            "item_id": items["lentils"]["id"],
            "paid": 15.0,
            "quantity": 1000,
            "source": "manual",
            "observed_on": _today().isoformat(),
            "note": "shelf price today",
        },
    )

    after = client.get(f"/api/prices/estimate/{items['lentils']['id']}").json()
    history_after = client.get(
        f"/api/prices/observations/{items['lentils']['id']}"
    ).json()

    assert after["price"] > before["price"]
    assert len(history_after) == history_before + 1, "nothing was overwritten"


def test_a_stale_price_is_flagged_and_findable(client, items):
    client.post(
        "/api/prices/observations",
        json={
            "item_id": items["tahini"]["id"],
            "paid": 30.0,
            "quantity": 1000,
            "source": "manual",
            "observed_on": (_today() - timedelta(days=120)).isoformat(),
        },
    )

    est = client.get(f"/api/prices/estimate/{items['tahini']['id']}").json()
    assert est["stale"] is True
    assert est["certainty"] == "poor"

    needs_checking = client.get("/api/prices/estimates?stale_only=true").json()
    assert any(row["name_en"] == "Tahini" for row in needs_checking)


def test_unknown_item_is_rejected(client):
    resp = client.post(
        "/api/prices/observations",
        json={"item_id": 999999, "paid": 5.0, "quantity": 1000},
    )
    assert resp.status_code == 404


def test_a_non_positive_price_is_rejected(client, items):
    resp = client.post(
        "/api/prices/observations",
        json={"item_id": items["rice"]["id"], "paid": 0, "quantity": 1000},
    )
    assert resp.status_code == 422


# --- costing a list ----------------------------------------------------------


def test_costing_a_list_names_its_own_gaps(client, dishes):
    week = date(2028, 4, 3)
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")
    client.post(
        "/api/kitchen/plan",
        json={"plan_date": week.isoformat(), "slot": "lunch", "dish_id": dishes["mujaddara"]},
    )
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    body = client.get(f"/api/prices/shopping-list/cost?week_start={week}").json()
    assert body["lines_priced"] >= 1
    assert body["lines_unpriced"] >= 1, "most of the catalogue has no price yet"
    assert body["known_cost"] > 0
    assert "Covers" in body["caveat"]
    assert str(body["lines_unpriced"]) in body["caveat"]


def test_an_unpriced_line_has_no_invented_cost(client, dishes):
    week = date(2028, 4, 3)
    body = client.get(f"/api/prices/shopping-list/cost?week_start={week}").json()
    unpriced = [line for line in body["lines"] if line["estimate"] is None]
    assert unpriced
    assert all(line["cost"] is None for line in unpriced)


def test_costing_an_empty_week_says_so(client):
    body = client.get("/api/prices/shopping-list/cost?week_start=2029-01-01").json()
    assert body["known_cost"] == 0
    assert body["lines_priced"] == 0
    assert "cannot be costed" in body["caveat"]


# --- prices reach the suggestions -------------------------------------------


def test_a_suggestion_says_what_the_missing_piece_costs(client, items):
    """"A different meal for the price of a spice" becomes an actual figure."""
    for slug, qty in (
        ("lentils", 6000), ("rice", 6000), ("carrot", 3000),
        ("potato", 4000), ("onion", 3000), ("salt", 800), ("veg_oil", 1000),
    ):
        client.post(
            "/api/pantry/stock",
            json={"item_id": items[slug]["id"], "quantity": qty, "source": "purchased"},
        )
    client.post(
        "/api/prices/observations",
        json={
            "item_id": items["tomato_paste"]["id"],
            "paid": 14.0,
            "quantity": 1000,
            "source": "manual",
            "observed_on": _today().isoformat(),
        },
    )

    ranked = client.get("/api/pantry/suggestions?limit=50").json()
    stew = next(s for s in ranked if s["name_en"] == "Lentil and vegetable stew")

    assert stew["flavour_only"]
    paste = next(m for m in stew["missing"] if m["name_en"] == "Tomato paste")
    assert paste["cost"] is not None
    assert stew["missing_cost"] == pytest.approx(paste["cost"], abs=0.01)
    assert stew["missing_cost_partial"] is False
    assert "for about" in stew["why"]


def test_a_partial_cost_is_flagged_rather_than_understated(client, items):
    ranked = client.get("/api/pantry/suggestions?limit=50").json()
    partial = [s for s in ranked if s["missing_cost_partial"]]
    assert partial, "most dishes have some unpriced ingredient"
    for s in partial:
        assert "for about" not in s["why"], "never quote a figure that omits items"
