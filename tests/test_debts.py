"""The debt ledger: what the household owes («عليكم») and is owed («إلكم»)."""

from datetime import date

import pytest

from app.models import LedgerEntry, LedgerKind
from app.services.ledger import running, standing


def _tab(client, amount, shop="بقالة أبو سمير", **kw):
    body = {"booked_on": "2031-03-05", "amount": -amount, "note": shop, "category": "groceries",
            "source": "credit", "party_name": shop}
    body.update(kw)
    resp = client.post("/api/transactions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _entry(client, **kw):
    resp = client.post("/api/debts/entries", json=kw)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _party(client, name):
    return next(p for p in client.get("/api/debts", params={"include_settled": True}).json()["parties"] if p["name"] == name)


# --- the arithmetic -------------------------------------------------------------------


def _e(kind, amount, on="2031-01-01", cur="ILS", due=None):
    return LedgerEntry(party_id=1, kind=kind, amount=amount, currency=cur, on=date.fromisoformat(on),
                       due_on=date.fromisoformat(due) if due else None)


def test_the_sign_is_the_households():
    rows = [_e(LedgerKind.BORROW, 100), _e(LedgerKind.REPAY, 40), _e(LedgerKind.LEND, 10, cur="JOD")]
    s = standing(rows, date(2031, 2, 1))
    assert s.balance == {"ILS": -60, "JOD": 10}, "negative: we owe; positive: they owe us"
    assert running(rows) == [-100, -60, 10]


def test_overdue_needs_a_passed_date_and_money_still_out():
    rows = [_e(LedgerKind.LEND, 50, due="2031-01-10")]
    assert standing(rows, date(2031, 1, 11)).overdue
    assert not standing(rows, date(2031, 1, 10)).overdue
    rows.append(_e(LedgerKind.COLLECT, 50, on="2031-01-12"))
    assert not standing(rows, date(2031, 1, 20)).overdue, "paid back, so nothing is late"


# --- goods on the tab, in one write ---------------------------------------------------------


def test_goods_on_the_tab_are_spending_and_a_debt(client):
    out = _tab(client, 45, key="tab-tap-1")
    assert out["ledger_entry_id"] and out["party_name"] == "بقالة أبو سمير"
    shop = _party(client, "بقالة أبو سمير")
    assert (shop["kind"], shop["balance"]) == ("shop", {"ILS": -45})
    summary = client.get("/api/summary", params={"month": "2031-03"}).json()
    assert summary["by_category"]["groceries"] == pytest.approx(45), "counted the day the goods were taken"

    again = client.post("/api/transactions", json={"booked_on": "2031-03-05", "amount": -45, "note": "بقالة أبو سمير",
                                                   "category": "groceries", "source": "credit",
                                                   "party_name": "بقالة أبو سمير", "key": "tab-tap-1"}).json()
    assert again["ledger_entry_id"] == out["ledger_entry_id"]
    assert _party(client, "بقالة أبو سمير")["balance"] == {"ILS": -45}, "a replay owes nothing twice"


def test_the_tab_follows_its_purchase(client):
    out = _tab(client, 30, shop="دكان الحارة")
    tid = out["transaction"]["id"]
    client.patch(f"/api/transactions/{tid}", json={"amount": -35, "booked_on": "2031-03-06"})
    detail = client.get(f"/api/debts/parties/{out['party_id']}").json()
    assert [(e["amount"], e["on"]) for e in detail["entries"]] == [(35, "2031-03-06")]
    assert client.patch(f"/api/transactions/{tid}", json={"amount": 35}).status_code == 422
    eid = out["ledger_entry_id"]
    assert client.delete(f"/api/debts/entries/{eid}").status_code == 409, "remove the purchase, not its debt"
    assert client.delete(f"/api/transactions/{tid}").status_code == 204
    assert _party(client, "دكان الحارة")["balance"] == {}


def test_a_tab_needs_a_shop_and_money_out(client):
    base = {"booked_on": "2031-03-05", "note": "x", "source": "credit"}
    assert client.post("/api/transactions", json={**base, "amount": -5}).status_code == 422
    assert client.post("/api/transactions", json={**base, "amount": 5, "party_name": "x"}).status_code == 422


# --- money between people ---------------------------------------------------------------


def test_lending_and_collecting(client):
    lent = _entry(client, party_name="خالي أبو محمد", kind="lend", amount=200, on="2031-04-01", due_on="2031-04-15")
    assert lent["party"]["balance"] == {"ILS": 200} and lent["party"]["kind"] == "person"
    pid = lent["party"]["id"]
    _entry(client, party_id=pid, kind="collect", amount=50, on="2031-04-10")
    late = client.get("/api/debts", params={"on": "2031-04-20"}).json()
    row = next(p for p in late["parties"] if p["id"] == pid)
    assert (row["balance"], row["overdue"]) == ({"ILS": 150}, True)
    assert late["parties"][0]["overdue"], "a missed date comes first"
    detail = client.get(f"/api/debts/parties/{pid}").json()
    assert [e["balance_after"] for e in detail["entries"]] == [200, 150]


def test_totals_split_by_direction_and_currency(client):
    _entry(client, party_name="جارتنا أم علي", kind="borrow", amount=20, currency="JOD")
    totals = client.get("/api/debts").json()
    assert totals["we_owe"]["JOD"] == pytest.approx(20)
    assert totals["owed_to_us"]["ILS"] >= 150
    assert totals["we_owe"]["ILS"] >= 45


def test_ledger_moves_are_not_spending(client):
    before = client.get("/api/summary").json()["total_out"]
    _entry(client, party_name="أختي نور", kind="lend", amount=500)
    assert client.get("/api/summary").json()["total_out"] == before


def test_paying_off_a_tab_is_not_counted_twice(client):
    before = client.get("/api/summary", params={"month": "2031-05"}).json()
    resp = client.post("/api/transactions", json={"booked_on": "2031-05-02", "amount": -45, "note": "تسديد دفتر أبو سمير",
                                                  "category": "debt_repayment"})
    assert resp.status_code == 200
    after = client.get("/api/summary", params={"month": "2031-05"}).json()
    assert after["total_out"] == before["total_out"]
    assert after["debt_repaid"] == pytest.approx(45)


def test_entries_can_be_fixed_and_removed(client):
    e = _entry(client, party_name="صديقي سامي", kind="borrow", amount=60, key="ledger-tap-1")
    assert _entry(client, party_name="صديقي سامي", kind="borrow", amount=60, key="ledger-tap-1")["entry"]["id"] == e["entry"]["id"]
    eid = e["entry"]["id"]
    assert client.patch(f"/api/debts/entries/{eid}", json={"amount": 70}).json()["amount"] == 70
    assert client.delete(f"/api/debts/entries/{eid}").status_code == 204
    assert client.delete(f"/api/debts/entries/{eid}").status_code == 404
    assert client.post("/api/debts/entries", json={"kind": "lend", "amount": 5}).status_code == 422, "say who"


def test_rename_a_party(client):
    pid = _entry(client, party_name="محل الحلويات", party_kind="shop", kind="borrow", amount=15)["party"]["id"]
    assert client.patch(f"/api/debts/parties/{pid}", json={"name": "حلويات السلطان"}).json()["name"] == "حلويات السلطان"
    assert client.patch(f"/api/debts/parties/{pid}", json={"name": "أختي نور"}).status_code == 409


def test_debts_travel_in_the_bundle(client):
    bundle = client.get("/api/bootstrap").json()
    assert bundle["debts"]["parties"] and "we_owe" in bundle["debts"]
