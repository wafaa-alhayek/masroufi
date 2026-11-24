"""Budgets per category, over a period the household picks."""

from datetime import date

import pytest

from app.models import Budget, BudgetPeriod
from app.services.budgets import window


def _spend(client, day, amount, category="groceries", **kw):
    body = {"booked_on": day, "amount": -amount, "note": "سوبر ماركت الهدى", "category": category}
    body.update(kw)
    resp = client.post("/api/transactions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["transaction"]


# --- the window, worked out on read ------------------------------------------------


@pytest.mark.parametrize(
    "period,days,starts,on,expected",
    [
        (BudgetPeriod.WEEK, None, date(2031, 3, 1), date(2031, 3, 9), (date(2031, 3, 8), date(2031, 3, 14))),
        (BudgetPeriod.FORTNIGHT, None, date(2031, 3, 1), date(2031, 3, 14), (date(2031, 3, 1), date(2031, 3, 14))),
        (BudgetPeriod.DAYS, 10, date(2031, 3, 1), date(2031, 3, 25), (date(2031, 3, 21), date(2031, 3, 30))),
        (BudgetPeriod.MONTH, None, date(2031, 1, 5), date(2031, 3, 4), (date(2031, 2, 5), date(2031, 3, 4))),
        (BudgetPeriod.MONTH, None, date(2031, 1, 5), date(2031, 3, 5), (date(2031, 3, 5), date(2031, 4, 4))),
        # From the 31st: February has no 31st, so its window starts on the 28th.
        (BudgetPeriod.MONTH, None, date(2031, 1, 31), date(2031, 3, 1), (date(2031, 2, 28), date(2031, 3, 30))),
        (BudgetPeriod.MONTH, None, date(2031, 12, 10), date(2032, 1, 2), (date(2031, 12, 10), date(2032, 1, 9))),
        # Before it starts, the first window.
        (BudgetPeriod.WEEK, None, date(2031, 3, 10), date(2031, 3, 1), (date(2031, 3, 10), date(2031, 3, 16))),
    ],
)
def test_the_window_repeats_from_the_start(period, days, starts, on, expected):
    b = Budget(category="groceries", amount=100, period=period, days=days, starts_on=starts)
    assert window(b, on) == expected


# --- setting one ----------------------------------------------------------------------


def test_a_fortnight_of_groceries(client):
    _spend(client, "2031-03-02", 150)
    _spend(client, "2031-03-04", 90)
    _spend(client, "2031-03-20", 999)  # the next fortnight
    _spend(client, "2031-03-03", 40, category="transport", note="تاكسي")
    b = client.post(
        "/api/budgets",
        json={"category": "groceries", "amount": 800, "period": "fortnight", "starts_on": "2031-03-01"},
    ).json()
    s = next(x for x in client.get("/api/budgets", params={"on": "2031-03-07"}).json() if x["id"] == b["id"])
    assert (s["window_from"], s["window_to"]) == ("2031-03-01", "2031-03-14")
    assert (s["spent"], s["left"]) == (240, 560)
    assert (s["days_total"], s["days_gone"], s["days_left"]) == (14, 7, 8)
    assert s["expected_by_now"] == pytest.approx(400)
    assert s["per_day_left"] == pytest.approx(70)
    assert s["status"] == "fine"


def test_fast_and_over(client):
    client.post("/api/budgets", json={"category": "water", "amount": 100, "period": "week", "starts_on": "2031-05-04"})
    _spend(client, "2031-05-05", 60, category="water", note="تانكر مياه")
    by_cat = lambda on: next(x for x in client.get("/api/budgets", params={"on": on}).json() if x["category"] == "water")
    assert by_cat("2031-05-05")["status"] == "fast", "60% gone on day two of seven"
    assert by_cat("2031-05-04")["status"] == "fine", "never fast before the second day"
    _spend(client, "2031-05-06", 50, category="water", note="تانكر مياه")
    over = by_cat("2031-05-06")
    assert (over["status"], over["left"], over["per_day_left"]) == ("over", -10, None)


def test_transfers_and_excluded_rows_do_not_count(client):
    client.post("/api/budgets", json={"category": "health", "amount": 200, "period": "month", "starts_on": "2031-06-01"})
    kept = _spend(client, "2031-06-03", 30, category="health", note="صيدلية")
    dropped = _spend(client, "2031-06-04", 70, category="health", note="صيدلية")
    client.patch(f"/api/transactions/{dropped['id']}", json={"excluded": True})
    s = next(x for x in client.get("/api/budgets", params={"on": "2031-06-10"}).json() if x["category"] == "health")
    assert s["spent"] == pytest.approx(kept["amount"] * -1)


def test_refusals(client):
    post = lambda **kw: client.post("/api/budgets", json={"amount": 50, **kw})
    assert post(category="nonsense").status_code == 422
    assert post(category="transfer_out").status_code == 422, "moving money is not spending"
    assert post(category="clothing", period="days").status_code == 422, "days needs a length"
    assert post(category="clothing", amount=0).status_code == 422
    assert post(category="clothing").status_code == 200
    assert post(category="clothing").status_code == 409, "one budget per category"


def test_a_replayed_set_is_harmless(client):
    body = {"category": "education", "amount": 300, "key": "budget-tap-1"}
    first = client.post("/api/budgets", json=body).json()
    again = client.post("/api/budgets", json=body)
    assert again.status_code == 200 and again.json()["id"] == first["id"]


def test_change_and_remove(client):
    b = client.post(
        "/api/budgets", json={"category": "communication", "amount": 60, "period": "days", "days": 10, "starts_on": "2031-07-01"}
    ).json()
    assert b["days"] == 10
    changed = client.patch(f"/api/budgets/{b['id']}", json={"period": "week", "amount": 40}).json()
    assert (changed["period"], changed["days"], changed["amount"]) == ("week", None, 40)
    assert client.patch(f"/api/budgets/{b['id']}", json={"period": "days"}).status_code == 422
    assert client.delete(f"/api/budgets/{b['id']}").status_code == 204
    assert client.delete(f"/api/budgets/{b['id']}").status_code == 404


def test_budgets_travel_in_the_bundle(client):
    bundle = client.get("/api/bootstrap").json()
    cats = {b["category"] for b in bundle["budgets"]}
    assert {"groceries", "water"} <= cats
    assert {"window_from", "window_to", "spent", "left", "status"} <= set(bundle["budgets"][0])
