"""One ordered list of questions, and the answers that settle a debt."""

import pytest


def _add(client, **kw):
    body = {"booked_on": "2031-08-10", "amount": -20.0, "note": "x"}
    body.update(kw)
    resp = client.post("/api/transactions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _questions(client, **params):
    resp = client.get("/api/questions", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_biggest_answer_first(client):
    for day in ("2031-08-01", "2031-08-03", "2031-08-05"):
        _add(client, booked_on=day, amount=-7, note="محل الزهراء للأدوات")
    _add(client, booked_on="2031-08-06", amount=-9, note="كشك الصباح")
    qs = _questions(client)["questions"]
    settles = [q["settles"] for q in qs]
    assert settles == sorted(settles, reverse=True)
    top = next(q for q in qs if "الزهراء" in str(q["params"]))
    assert top["settles"] >= 3
    for q in qs:
        assert "prompt" not in q["params"], "templates live in the client"


def test_a_payment_that_names_the_tab_is_asked_about(client):
    tab = _add(client, booked_on="2031-09-01", amount=-80, note="بقالة أبو سمير", category="groceries",
               source="credit", party_name="أبو سمير البقال")
    pid = tab["party_id"]
    paid = _add(client, booked_on="2031-09-20", amount=-50, note="تسديد دفتر سمير البقال", source="jawwalpay")
    _add(client, booked_on="2031-09-20", amount=-50, note="محطة وقود", source="jawwalpay")  # same amount, no name
    qs = [q for q in _questions(client)["questions"] if q["kind"] == "debt_payment"]
    assert [q["params"]["transaction_id"] for q in qs] == [paid["transaction"]["id"]], "the name has to match"
    q = qs[0]
    assert (q["params"]["party_id"], q["params"]["owed"]) == (pid, 80)

    before = client.get("/api/summary", params={"month": "2031-09"}).json()["total_out"]
    ans = client.post("/api/questions/debt-payment", json={"transaction_id": paid["transaction"]["id"], "party_id": pid})
    assert ans.status_code == 200, ans.text
    assert ans.json()["party"]["balance"] == {"ILS": -30}
    after = client.get("/api/summary", params={"month": "2031-09"}).json()
    assert after["total_out"] == pytest.approx(before - 50), "paying the tab is not new spending"
    again = client.post("/api/questions/debt-payment", json={"transaction_id": paid["transaction"]["id"], "party_id": pid})
    assert again.json()["ledger_entry_id"] == ans.json()["ledger_entry_id"], "a replay changes nothing"
    assert not [q for q in _questions(client)["questions"] if q["kind"] == "debt_payment"]

    client.delete(f"/api/transactions/{paid['transaction']['id']}")
    assert next(p for p in client.get("/api/debts").json()["parties"] if p["id"] == pid)["balance"] == {"ILS": -80}


def test_no_is_remembered(client):
    tab = _add(client, booked_on="2031-10-01", amount=-40, note="مخبز العائلة", category="groceries",
               source="credit", party_name="مخبز العائلة")
    pay = _add(client, booked_on="2031-10-05", amount=-40, note="مخبز العائلة", source="palpay")
    key = f"debt:{pay['transaction']['id']}:{tab['party_id']}"
    assert key in {q["key"] for q in _questions(client)["questions"]}
    assert client.post("/api/questions/dismiss", json={"key": key}).status_code == 200
    assert client.post("/api/questions/dismiss", json={"key": key}).status_code == 200, "a replay is harmless"
    assert key not in {q["key"] for q in _questions(client)["questions"]}
    assert client.post("/api/questions/dismiss", json={"key": "vendor:1"}).status_code == 422


def test_more_than_owed_is_not_a_match(client):
    tab = _add(client, booked_on="2031-11-01", amount=-10, note="دكان الحاج رمضان", category="groceries",
               source="credit", party_name="الحاج رمضان")
    _add(client, booked_on="2031-11-02", amount=-300, note="الحاج رمضان", source="palpay")
    assert not [q for q in _questions(client)["questions"]
                if q["kind"] == "debt_payment" and q["params"]["party_id"] == tab["party_id"]]


def test_answers_report_what_they_settled(client):
    out = _add(client, booked_on="2031-12-01", amount=-120, note="xfer", source="bop")
    inn = _add(client, booked_on="2031-12-01", amount=120, note="incoming", source="palpay")
    resp = client.post("/api/transfers/link", json={"out_id": out["transaction"]["id"], "in_id": inn["transaction"]["id"]})
    assert resp.json()["also_settled"] == 2


def test_the_month_switcher_knows_which_months_exist(client):
    months = client.get("/api/summary").json()["months_available"]
    assert months[0] == "2031-12" and "2031-08" in months
    assert months == sorted(months, reverse=True)
