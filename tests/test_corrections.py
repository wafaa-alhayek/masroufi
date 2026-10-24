"""Transactions a household can fix: who it was for, what for, a name for «other»,
a wrong amount or date, a row to delete, and a month to look at."""

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from app.db import add_missing_columns

MONTH = "2031-03"


def _add(client, **kw):
    body = {"booked_on": "2031-03-10", "amount": -20.0, "note": "سوبر ماركت الهدى"}
    body.update(kw)
    resp = client.post("/api/transactions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- who, what for, and «other» with a name ---------------------------------------


def test_transport_for_israa_with_details(client):
    t = _add(client, note="تاكسي", amount=-6, category="transport", for_whom="إسراء", details="للجامعة")["transaction"]
    assert (t["for_whom"], t["details"]) == ("إسراء", "للجامعة")
    assert "إسراء" in {p["name"] for p in client.get("/api/people").json()}, "first use adds the person"
    assert client.get("/api/summary", params={"month": MONTH}).json()["by_person"]["إسراء"] >= 6


def test_other_carries_its_own_name(client):
    t = _add(client, note="محل تصليح", amount=-70, category="other", category_label="تصليح موبايل")["transaction"]
    assert t["category_label"] == "تصليح موبايل"
    s = client.get("/api/summary", params={"month": MONTH}).json()
    assert s["other_labels"]["تصليح موبايل"] == pytest.approx(70)


def test_a_name_is_only_for_other(client):
    resp = client.post(
        "/api/transactions",
        json={"booked_on": "2031-03-10", "amount": -5, "note": "خبز", "category": "groceries", "category_label": "خبز"},
    )
    assert resp.status_code == 422


def test_a_named_other_teaches_the_shop(client):
    first = _add(client, note="محل الأدوات المنزلية", amount=-30)
    tid, vid = first["transaction"]["id"], first["vendor_id"]
    resp = client.patch(
        f"/api/transactions/{tid}/category",
        json={"category": "other", "category_label": "أدوات بيت"},
    )
    assert resp.status_code == 200, resp.text
    vendor = next(v for v in client.get("/api/vendors").json() if v["id"] == vid)
    assert (vendor["category"], vendor["category_label"], vendor["category_confirmed"]) == ("other", "أدوات بيت", True)
    again = _add(client, note="محل الأدوات المنزلية", amount=-12)["transaction"]
    assert (again["category"], again["category_label"]) == ("other", "أدوات بيت"), "the shop settles the next one"


# --- fixing what was typed ---------------------------------------------------------


def test_fix_the_amount_date_and_who(client):
    t = _add(client, note="بقالة النجمة", amount=-45)
    tid, vid = t["transaction"]["id"], t["vendor_id"]
    resp = client.patch(
        f"/api/transactions/{tid}",
        json={"amount": -54, "booked_on": "2031-03-11", "for_whom": "يوسف", "details": "دفتر المدرسة"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["amount"], body["booked_on"], body["for_whom"], body["details"]) == (-54, "2031-03-11", "يوسف", "دفتر المدرسة")
    vendor = next(v for v in client.get("/api/vendors").json() if v["id"] == vid)
    assert vendor["total_spent"] == pytest.approx(54), "the shop's total follows the fix"


def test_a_fixed_note_finds_the_right_shop(client):
    known = _add(client, note="صيدلية النور", amount=-10)
    client.post(f"/api/vendors/{known['vendor_id']}/confirm", json={"category": "health"})
    typo = _add(client, note="مكان ما", amount=-15)
    body = client.patch(f"/api/transactions/{typo['transaction']['id']}", json={"note": "صيدلية النور"}).json()
    assert body["vendor_id"] == known["vendor_id"]
    assert (body["category"], body["needs_review"]) == ("health", False)


def test_zero_and_a_wrong_label_are_refused(client):
    tid = _add(client)["transaction"]["id"]
    assert client.patch(f"/api/transactions/{tid}", json={"amount": 0}).status_code == 422
    assert client.patch(f"/api/transactions/{tid}", json={"category_label": "x"}).status_code == 422
    assert client.patch("/api/transactions/999999", json={"details": "x"}).status_code == 404


def test_delete_is_the_undo(client):
    t = _add(client, note="بقالة الصباح", amount=-33)
    tid, vid = t["transaction"]["id"], t["vendor_id"]
    assert client.delete(f"/api/transactions/{tid}").status_code == 204
    assert tid not in {r["id"] for r in client.get("/api/transactions").json()}
    vendor = next(v for v in client.get("/api/vendors").json() if v["id"] == vid)
    assert (vendor["times_seen"], vendor["total_spent"]) == (0, 0)
    assert client.delete(f"/api/transactions/{tid}").status_code == 404, "a replayed delete finds it gone"


def test_deleting_one_side_of_a_transfer_frees_the_other(client):
    day = "2031-03-20"
    out = _add(client, booked_on=day, amount=-150, note="تحويل الى JawwalPay", source="bop")
    inn = _add(client, booked_on=day, amount=150, note="top up", source="jawwalpay")
    assert inn["transfers_linked"] == 1
    assert client.delete(f"/api/transactions/{out['transaction']['id']}").status_code == 204
    peer = next(r for r in client.get("/api/transactions").json() if r["id"] == inn["transaction"]["id"])
    assert peer["is_internal_transfer"] is False


# --- an imported row is excluded, never deleted ---------------------------------------


def test_an_imported_row_is_excluded_not_deleted(client):
    statement = "Date,Description,Debit,Credit,Currency\n2031-05-07,مطعم الساحة,80.00,,ILS\n".encode()
    assert client.post("/api/import", files={"file": ("may.csv", statement, "text/csv")}).json()["imported"] == 1
    row = next(r for r in client.get("/api/transactions", params={"month": "2031-05"}).json())
    assert client.delete(f"/api/transactions/{row['id']}").status_code == 409, "it would come back on re-import"

    resp = client.patch(f"/api/transactions/{row['id']}", json={"excluded": True})
    assert resp.status_code == 200 and resp.json()["excluded"] is True
    may = client.get("/api/summary", params={"month": "2031-05"}).json()
    assert (may["total_out"], may["excluded"]) == (0, 1)
    vendor = next(v for v in client.get("/api/vendors").json() if v["id"] == row["vendor_id"])
    assert vendor["total_spent"] == 0

    again = client.post("/api/import", files={"file": ("may.csv", statement, "text/csv")}).json()
    assert (again["imported"], again["duplicates_skipped"]) == (0, 1), "the excluded row still counts as held"

    client.patch(f"/api/transactions/{row['id']}", json={"excluded": False})
    assert client.get("/api/summary", params={"month": "2031-05"}).json()["total_out"] == pytest.approx(80)


# --- a month at a time ------------------------------------------------------------------


def test_the_summary_and_list_take_a_month(client):
    _add(client, booked_on="2031-04-02", amount=-999, note="سوبر ماركت الهدى")
    march = client.get("/api/summary", params={"month": MONTH}).json()
    april = client.get("/api/summary", params={"month": "2031-04"}).json()
    assert (march["from"], march["to"]) == ("2031-03-01", "2031-03-31")
    assert april["total_out"] == pytest.approx(999)
    assert march["total_out"] < 999 < march["total_out"] + april["total_out"]
    rows = client.get("/api/transactions", params={"month": "2031-04"}).json()
    assert [r["amount"] for r in rows] == [-999]
    assert client.get("/api/summary", params={"month": "2031-13"}).status_code == 422


# --- people ------------------------------------------------------------------------------


def test_people_add_once_and_rename_everywhere(client):
    a = client.post("/api/people", json={"name": "محمد"}).json()
    assert client.post("/api/people", json={"name": "محمد"}).json()["id"] == a["id"], "a replay is harmless"
    tid = _add(client, for_whom="محمد")["transaction"]["id"]
    renamed = client.patch(f"/api/people/{a['id']}", json={"name": "محمود"})
    assert renamed.status_code == 200
    row = next(r for r in client.get("/api/transactions").json() if r["id"] == tid)
    assert row["for_whom"] == "محمود"
    client.post("/api/people", json={"name": "سارة"})
    assert client.patch(f"/api/people/{a['id']}", json={"name": "سارة"}).status_code == 409


# --- an install from before these fields ------------------------------------------------


def test_an_older_database_gets_the_new_columns():
    from app import idempotency  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO "transaction" (booked_on, amount, currency, note, note_key, note_clean, '
                          'note_translated, is_internal_transfer, category_source, needs_review, import_batch, created_at, '
                          'for_whom, details, category_label, excluded) VALUES '
                          "('2031-01-01', -5, 'ILS', 'x', 'x', '', 0, 0, 'UNSET', 1, 'old', '2031-01-01', '', '', '', 0)"))
        conn.execute(text("DROP INDEX ix_transaction_for_whom"))
        conn.execute(text('ALTER TABLE "transaction" DROP COLUMN for_whom'))
        conn.execute(text('ALTER TABLE "transaction" DROP COLUMN details'))
        conn.execute(text("DROP INDEX ix_transaction_excluded"))
        conn.execute(text('ALTER TABLE "transaction" DROP COLUMN excluded'))
    added = add_missing_columns(engine)
    assert {"transaction.for_whom", "transaction.details", "transaction.excluded"} <= set(added)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT for_whom, details, excluded FROM "transaction"')).one() == ("", "", 0)
        indexes = {r[1] for r in conn.execute(text("PRAGMA index_list('transaction')"))}
        assert "ix_transaction_for_whom" in indexes
    assert add_missing_columns(engine) == [], "running it again adds nothing"
