"""One user, one list: the household's money and the user's own («البيت | إلي»)."""

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from app.db import relax_unique_indexes

MONTH = "2032-02"


def _add(client, **kw):
    body = {"booked_on": "2032-02-10", "amount": -20.0, "note": "بقالة النجمة", "category": "groceries"}
    body.update(kw)
    resp = client.post("/api/transactions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["transaction"]


def test_the_first_launch_question(client):
    assert client.get("/api/kitchen/household").json()["mode"] is None, "not asked yet"
    alone = client.patch("/api/kitchen/household", json={"mode": "alone"}).json()
    assert (alone["mode"], alone["adults"], alone["children"]) == ("alone", 1, 0), "cooks for one"
    assert client.get("/api/bootstrap").json()["household"]["mode"] == "alone"
    back = client.patch("/api/kitchen/household", json={"mode": "household", "adults": 4}).json()
    assert (back["mode"], back["adults"]) == ("household", 4)


def test_the_summary_splits_house_and_me(client):
    _add(client, amount=-100)
    _add(client, amount=-30, note="رصيد جوال", category="communication", personal=True)
    _add(client, amount=-6, note="تاكسي", category="transport", for_whom="إسراء")

    every = client.get("/api/summary", params={"month": MONTH}).json()
    assert every["total_out"] == pytest.approx(136)
    assert every["by_scope"] == {"household": pytest.approx(106), "personal": pytest.approx(30)}

    mine = client.get("/api/summary", params={"month": MONTH, "scope": "personal"}).json()
    assert (mine["scope"], mine["total_out"]) == ("personal", pytest.approx(30))
    assert mine["by_scope"]["household"] == pytest.approx(106), "the switch shows both sides"

    house = client.get("/api/summary", params={"month": MONTH, "scope": "household"}).json()
    assert house["total_out"] == pytest.approx(106)
    assert house["by_person"]["إسراء"] == pytest.approx(6), "spent on a family member is household money"


def test_the_list_takes_a_scope(client):
    rows = client.get("/api/transactions", params={"month": MONTH, "scope": "personal"}).json()
    assert rows and all(r["personal"] for r in rows)


def test_mine_cannot_also_be_for_someone(client):
    resp = client.post(
        "/api/transactions",
        json={"booked_on": "2032-02-11", "amount": -5, "note": "قهوة", "personal": True, "for_whom": "يوسف"},
    )
    assert resp.status_code == 422
    t = _add(client, amount=-9, note="قهوة", category="other", for_whom="يوسف")
    assert client.patch(f"/api/transactions/{t['id']}", json={"personal": True}).status_code == 422
    moved = client.patch(f"/api/transactions/{t['id']}", json={"personal": True, "for_whom": ""})
    assert moved.status_code == 200 and moved.json()["personal"] is True


def test_a_budget_per_scope(client):
    house = client.post(
        "/api/budgets",
        json={"category": "groceries", "amount": 500, "scope": "household", "starts_on": "2032-02-01"},
    )
    mine = client.post(
        "/api/budgets",
        json={"category": "groceries", "amount": 50, "scope": "personal", "starts_on": "2032-02-01"},
    )
    assert house.status_code == 200 and mine.status_code == 200, "same category, two scopes"
    again = client.post("/api/budgets", json={"category": "groceries", "amount": 60, "scope": "personal"})
    assert again.status_code == 409

    _add(client, amount=-12, note="بزر", personal=True)
    by_scope = {
        b["scope"]: b
        for b in client.get("/api/budgets", params={"on": "2032-02-15"}).json()
        if b["category"] == "groceries"
    }
    assert by_scope["personal"]["spent"] == pytest.approx(12)
    assert by_scope["household"]["spent"] == pytest.approx(100)


def test_an_older_database_allows_a_second_scope():
    from app import idempotency  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        # As it was when a budget was one per category.
        conn.execute(text("DROP INDEX ix_budget_category"))
        conn.execute(text('CREATE UNIQUE INDEX ix_budget_category ON budget (category)'))
    assert relax_unique_indexes(engine) == ["ix_budget_category"]
    with engine.begin() as conn:
        for scope in ("ALL", "PERSONAL"):
            conn.execute(
                text(
                    "INSERT INTO budget (category, scope, amount, currency, period, starts_on, created_at) "
                    f"VALUES ('groceries', '{scope}', 100, 'ILS', 'MONTH', '2032-01-01', '2032-01-01')"
                )
            )
    assert relax_unique_indexes(engine) == [], "running it again changes nothing"
