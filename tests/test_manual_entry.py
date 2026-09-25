"""Entering a transaction by hand.

The first way in, not a fallback. A household whose wallet has no export at all —
which may be most of them — has to be able to use the whole money side from day
one, so a hand-entered row has to build the same vendor dataset, get the same
patterns detected, and reach the same review queue as an imported one.
"""

from datetime import date, timedelta

import pytest

TODAY = date(2029, 9, 3)


def _entry(**kw) -> dict:
    body = {
        "booked_on": TODAY.isoformat(),
        "amount": -145.50,
        "note": "سوبر ماركت الأمل",
    }
    body.update(kw)
    return body


# --- the basic path -----------------------------------------------------------


def test_a_hand_entered_row_is_a_real_transaction(client):
    resp = client.post("/api/transactions", json=_entry())
    assert resp.status_code == 200, resp.text
    body = resp.json()

    t = body["transaction"]
    assert t["amount"] == pytest.approx(-145.50)
    assert t["note"] == "سوبر ماركت الأمل"
    assert t["id"] in {row["id"] for row in client.get("/api/transactions").json()}


def test_it_goes_through_the_same_vendor_path_as_an_import(client):
    """A manual household builds the same dataset, just more slowly."""
    body = client.post("/api/transactions", json=_entry(note="بقالة أبو خالد")).json()
    assert body["vendor_id"] is not None
    assert body["vendor_name"] == "بقالة أبو خالد"

    names = {v["name"] for v in client.get("/api/vendors").json()}
    assert "بقالة أبو خالد" in names


def test_the_model_categorises_it_when_the_household_does_not(client):
    body = client.post("/api/transactions", json=_entry(note="صيدلية الشفاء")).json()
    assert body["categorised_by"] == "model"
    assert body["transaction"]["category"] == "health"


def test_a_household_that_knows_skips_the_model_entirely(client):
    """The cheapest path in the app: no call, no question."""
    body = client.post(
        "/api/transactions", json=_entry(note="محل مجهول تماما", category="clothing")
    ).json()
    assert body["categorised_by"] == "user"
    assert body["transaction"]["category"] == "clothing"
    assert body["needs_review"] is False


def test_an_unreadable_note_lands_in_the_review_queue(client):
    body = client.post("/api/transactions", json=_entry(note="ABC 991")).json()
    assert body["needs_review"] is True
    queued = {t["id"] for t in client.get("/api/transactions?needs_review=true").json()}
    assert body["transaction"]["id"] in queued


def test_a_confirmed_vendor_settles_a_manual_entry_with_no_model_call(client):
    first = client.post("/api/transactions", json=_entry(note="فرن الخير")).json()
    client.post(
        f"/api/vendors/{first['vendor_id']}/confirm", json={"category": "groceries"}
    )

    again = client.post(
        "/api/transactions",
        json=_entry(note="فرن الخير", booked_on=(TODAY + timedelta(days=4)).isoformat()),
    ).json()

    assert again["settled_by_vendor"] is True
    assert again["categorised_by"] == "rule"
    assert again["needs_review"] is False


def test_money_in_can_be_entered_too(client):
    body = client.post(
        "/api/transactions", json=_entry(amount=1200.0, note="حوالة واردة")
    ).json()
    assert body["transaction"]["amount"] == pytest.approx(1200.0)

    summary = client.get("/api/summary").json()
    assert summary["total_in"] >= 1200.0


def test_a_source_can_be_named_for_money_that_came_from_one(client):
    body = client.post(
        "/api/transactions", json=_entry(note="دفعة جوال باي", source="jawwalpay")
    ).json()
    slugs = {s["slug"]: s["id"] for s in client.get("/api/sources").json()}
    assert body["transaction"]["source_id"] == slugs["jawwalpay"]


# --- what it refuses ----------------------------------------------------------


def test_a_zero_amount_is_refused(client):
    assert client.post("/api/transactions", json=_entry(amount=0)).status_code == 422


def test_an_empty_note_is_refused(client):
    assert client.post("/api/transactions", json=_entry(note="")).status_code == 422


def test_an_unknown_category_is_refused(client):
    resp = client.post("/api/transactions", json=_entry(category="yacht_upkeep"))
    assert resp.status_code == 422


def test_an_unknown_source_is_refused_and_names_the_real_ones(client):
    resp = client.post("/api/transactions", json=_entry(source="notawallet"))
    assert resp.status_code == 422
    assert "jawwalpay" in resp.json()["detail"]


# --- duplicates and replays ---------------------------------------------------


def test_two_identical_entries_are_two_transactions(client):
    """Deliberately not deduplicated: two ₪3 fares on one day are two fares, and
    someone who typed it twice meant it."""
    fare = _entry(amount=-3.0, note="تاكسي", booked_on="2029-09-10")
    first = client.post("/api/transactions", json=fare).json()
    second = client.post("/api/transactions", json=fare).json()
    assert first["transaction"]["id"] != second["transaction"]["id"]


def test_a_replayed_entry_is_recorded_once(client):
    """A double tap, or an offline queue replaying, must not invent spending."""
    fare = _entry(amount=-3.0, note="تاكسي", booked_on="2029-09-11", key="tap-manual-1")

    before = len(client.get("/api/transactions?limit=1000").json())
    first = client.post("/api/transactions", json=fare)
    second = client.post("/api/transactions", json=fare)

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()
    assert len(client.get("/api/transactions?limit=1000").json()) == before + 1


# --- it reaches everything else ----------------------------------------------


def test_hand_entered_rows_get_patterns_detected(client):
    """The ₪3 detector works on what a household typed, not just on an export."""
    for offset in range(6):
        client.post(
            "/api/transactions",
            json=_entry(
                amount=-3.0,
                note="مواصلات",
                booked_on=(date(2029, 10, 1) + timedelta(days=offset * 2)).isoformat(),
            ),
        )

    patterns = client.get("/api/patterns").json()
    assert any(
        abs(p["amount"]) == 3.0 and p["occurrences"] >= 5 for p in patterns
    ), "a manual household gets the same recurring-cost detection"


def test_a_hand_entered_top_up_is_still_an_internal_transfer(client):
    """Moving your own money is moving your own money, however it was recorded."""
    day = "2029-11-05"
    before = client.get("/api/summary").json()["total_out"]

    client.post(
        "/api/transactions",
        json={
            "booked_on": day,
            "amount": -200.0,
            "note": "تحويل الى JawwalPay",
            "source": "bop",
        },
    )
    body = client.post(
        "/api/transactions",
        json={"booked_on": day, "amount": 200.0, "note": "top up", "source": "jawwalpay"},
    ).json()

    assert body["transfers_linked"] == 1
    after = client.get("/api/summary").json()
    assert after["total_out"] == pytest.approx(before), "the 200 is not spending"
    assert after["internal_transfers_excluded"] >= 200.0


def test_a_manual_entry_can_be_corrected_like_any_other(client):
    body = client.post("/api/transactions", json=_entry(note="XYZ 4412")).json()
    resp = client.patch(
        f"/api/transactions/{body['transaction']['id']}/category",
        json={"category": "transport"},
    )
    assert resp.status_code == 200
    assert resp.json()["transaction"]["category_source"] == "user"
