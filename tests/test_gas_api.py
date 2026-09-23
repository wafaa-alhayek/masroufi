from datetime import date, timedelta

import pytest


WEEK = date(2026, 10, 5)


@pytest.fixture(scope="module")
def dishes(client):
    return {d["slug"]: d["id"] for d in client.get("/api/kitchen/dishes").json()}


def _clear(client, week: date) -> None:
    for existing in client.get(f"/api/kitchen/plan?week_start={week}").json():
        client.delete(f"/api/kitchen/plan/{existing['id']}")


def _plan(client, week: date, dish_id: int, days: int, slot="lunch") -> None:
    for offset in range(days):
        client.post(
            "/api/kitchen/plan",
            json={
                "plan_date": (week + timedelta(days=offset)).isoformat(),
                "slot": slot,
                "dish_id": dish_id,
            },
        )


def test_dishes_ranked_by_gas_cheapest_first(client):
    rows = client.get("/api/kitchen/dishes/gas").json()
    assert rows
    assert rows == sorted(rows, key=lambda r: r["kg"])
    assert rows[0]["kg"] == 0.0, "the no-cook dish should be cheapest"
    assert rows[0]["dish_name"] == "Bread with zaatar and oil"


def test_the_bean_stew_is_among_the_most_expensive_to_cook(client):
    rows = client.get("/api/kitchen/dishes/gas").json()
    assert rows[-1]["kg"] > rows[0]["kg"]
    top_three = {r["dish_name"] for r in rows[-3:]}
    assert "White bean stew" in top_three


def test_soaking_advice_appears_only_on_pulse_dishes(client):
    rows = {r["dish_name"]: r for r in client.get("/api/kitchen/dishes/gas").json()}
    assert rows["White bean stew"]["soaking_saves_kg"] > 0
    assert rows["Eggs with tomato"]["soaking_saves_kg"] is None


def test_filtering_by_gas_is_how_you_cook_on_a_short_cylinder(client):
    everything = client.get("/api/kitchen/dishes/gas").json()
    cheap = client.get("/api/kitchen/dishes/gas?max_kg=0.1").json()

    assert 0 < len(cheap) < len(everything)
    assert all(r["kg"] <= 0.1 for r in cheap)


def test_filtering_by_slot(client):
    rows = client.get("/api/kitchen/dishes/gas?slot=breakfast").json()
    assert rows
    names = {r["dish_name"] for r in rows}
    assert "Maqluba with chicken" not in names


def test_week_gas_totals_the_plan(client, dishes):
    _clear(client, WEEK)
    _plan(client, WEEK, dishes["fasolia"], 7)

    body = client.get(f"/api/kitchen/plan/gas?week_start={WEEK}").json()
    assert len(body["by_meal"]) == 7
    assert body["total_kg"] > 0
    assert body["per_day_kg"] == pytest.approx(body["total_kg"] / 7, abs=0.001)
    assert body["budget_kg"] is None


def test_a_week_of_beans_costs_more_than_a_week_of_eggs(client, dishes):
    _clear(client, WEEK)
    _plan(client, WEEK, dishes["fasolia"], 7)
    beans = client.get(f"/api/kitchen/plan/gas?week_start={WEEK}").json()["total_kg"]

    _clear(client, WEEK)
    _plan(client, WEEK, dishes["eggs_tomato"], 7)
    eggs = client.get(f"/api/kitchen/plan/gas?week_start={WEEK}").json()["total_kg"]

    assert beans > eggs * 2


def test_budget_warns_when_the_plan_exceeds_the_cylinder(client, dishes):
    _clear(client, WEEK)
    _plan(client, WEEK, dishes["fasolia"], 7)

    resp = client.post(
        "/api/kitchen/gas-budget",
        json={
            "starts_on": WEEK.isoformat(),
            "ends_on": (WEEK + timedelta(days=6)).isoformat(),
            "kg_available": 0.3,
            "price_per_kg": 25.0,
        },
    )
    assert resp.status_code == 200

    body = client.get(f"/api/kitchen/plan/gas?week_start={WEEK}").json()
    assert body["budget_kg"] == 0.3
    assert body["remaining_kg"] < 0
    assert body["warning"] is not None
    assert "short by" in body["warning"]
    assert "Soaking" in body["warning"], "the warning should say how to cut the gas"
    assert body["total_cost"] == pytest.approx(body["total_kg"] * 25.0, rel=1e-2)
    assert body["days_of_gas"] is not None


def test_no_warning_when_the_gas_is_sufficient(client, dishes):
    later = date(2026, 11, 2)
    _clear(client, later)
    _plan(client, later, dishes["eggs_tomato"], 3)

    client.post(
        "/api/kitchen/gas-budget",
        json={
            "starts_on": later.isoformat(),
            "ends_on": (later + timedelta(days=6)).isoformat(),
            "kg_available": 12.0,
        },
    )
    body = client.get(f"/api/kitchen/plan/gas?week_start={later}").json()
    assert body["warning"] is None
    assert body["remaining_kg"] > 0


def test_backwards_period_is_rejected(client):
    resp = client.post(
        "/api/kitchen/gas-budget",
        json={"starts_on": "2026-10-10", "ends_on": "2026-10-01", "kg_available": 5},
    )
    assert resp.status_code == 422


def test_zero_gas_is_rejected(client):
    resp = client.post(
        "/api/kitchen/gas-budget",
        json={"starts_on": "2026-10-01", "ends_on": "2026-10-07", "kg_available": 0},
    )
    assert resp.status_code == 422


def test_empty_week_needs_no_gas(client):
    empty = date(2026, 12, 7)
    _clear(client, empty)
    body = client.get(f"/api/kitchen/plan/gas?week_start={empty}").json()
    assert body["total_kg"] == 0.0
    assert body["by_meal"] == []


# --- storage advice ----------------------------------------------------------


def test_storage_advice_reports_the_temperature_and_its_source(client):
    body = client.get("/api/kitchen/storage-advice?on=2026-08-05").json()
    assert body["max_c"] > 0
    assert body["source"] == "climate-normals", "weather is off by default"
    assert body["temperature_estimated"] is True


def test_storage_advice_flags_what_the_heat_shortens(client):
    august = client.get("/api/kitchen/storage-advice?on=2026-08-05").json()
    by_name = {i["name_en"]: i for i in august["items"]}

    assert by_name["Bread"]["buy_daily"] is True
    assert by_name["Bread"]["shortened_by_heat"] is True
    assert by_name["Bread"]["keeps_days"] < by_name["Bread"]["keeps_days_at_20c"]
    assert by_name["Rice"]["buy_daily"] is False


def test_advice_is_quieter_in_winter_than_in_summer(client):
    january = client.get("/api/kitchen/storage-advice?on=2026-01-15").json()
    august = client.get("/api/kitchen/storage-advice?on=2026-08-15").json()

    assert january["max_c"] < august["max_c"]
    assert len(january["notes"]) < len(august["notes"])


def test_advice_only_mentions_items_worth_mentioning(client):
    body = client.get("/api/kitchen/storage-advice?on=2026-08-15").json()
    assert body["notes"], "something should be at risk at 31 C"
    assert not any("Salt" in note for note in body["notes"])


def test_advice_can_be_scoped_to_one_week_list(client, dishes):
    week = date(2027, 1, 4)
    _clear(client, week)
    _plan(client, week, dishes["zaatar_bread"], 3, slot="breakfast")
    client.post(f"/api/kitchen/shopping-list?week_start={week}")

    scoped = client.get(
        f"/api/kitchen/storage-advice?on=2027-01-06&week_start={week}"
    ).json()
    everything = client.get("/api/kitchen/storage-advice?on=2027-01-06").json()

    assert 0 < len(scoped["items"]) < len(everything["items"])
    assert {i["name_en"] for i in scoped["items"]} <= {"Bread", "Zaatar", "Olive oil"}
