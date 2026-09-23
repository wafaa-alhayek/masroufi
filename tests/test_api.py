from pathlib import Path

import pytest


SAMPLE = Path(__file__).parent.parent / "data" / "sample_statement.csv"


@pytest.fixture(scope="module")
def imported(client):
    resp = client.post(
        "/api/import",
        files={"file": ("statement.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_import_summary(imported):
    assert imported["imported"] == 31
    assert imported["auto_categorised"] > 20
    assert imported["needs_review"] >= 1


def test_repeat_vendors_avoid_repeat_classifier_calls(imported):
    # The sample has the same grocer, water tanker and top-up several times over.
    assert imported["classifier_calls_saved"] > 10


def test_review_queue_holds_the_unrecognisable_notes(client, imported):
    notes = {t["note"] for t in client.get("/api/transactions?needs_review=true").json()}
    assert "MISC DEBIT 7781" in notes
    assert "UNKNOWN 5512" in notes


def test_user_correction_sticks(client, imported):
    queued = client.get("/api/transactions?needs_review=true").json()
    target = queued[0]

    resp = client.patch(
        f"/api/transactions/{target['id']}/category", json={"category": "transport"}
    )
    assert resp.status_code == 200
    body = resp.json()["transaction"]
    assert body["category"] == "transport"
    assert body["category_source"] == "user"
    assert body["needs_review"] is False


def test_unknown_category_is_rejected(client, imported):
    target = client.get("/api/transactions").json()[0]
    resp = client.patch(
        f"/api/transactions/{target['id']}/category", json={"category": "yacht_maintenance"}
    )
    assert resp.status_code == 422


def test_patterns_surface_the_three_shekel_fare(client, imported):
    patterns = client.get("/api/patterns").json()
    amount_only = [p for p in patterns if p["by_amount_only"]]
    assert any(abs(p["amount"]) == 3.0 and p["occurrences"] >= 5 for p in amount_only)


def test_confirming_a_pattern_clears_the_whole_group(client, imported):
    before = len(client.get("/api/transactions?needs_review=true").json())

    resp = client.post(
        "/api/patterns/confirm",
        json={"amount": -3.0, "currency": "ILS", "note_key": "", "category": "transport"},
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["transactions_updated"]
    assert updated >= 5, "one answer should settle the whole ₪3 group"

    after = len(client.get("/api/transactions?needs_review=true").json())
    assert after == before - updated


def test_confirming_an_unknown_pattern_is_404(client, imported):
    resp = client.post(
        "/api/patterns/confirm",
        json={"amount": -999.99, "currency": "ILS", "note_key": "", "category": "transport"},
    )
    assert resp.status_code == 404


def test_vendor_dataset_is_built_from_the_notes(client, imported):
    vendors = client.get("/api/vendors").json()
    names = {v["name"] for v in vendors}
    assert "سوبر ماركت الأمل" in names, "the tidied vendor name, not the raw note"
    assert imported["vendors_known"] == len(vendors)

    grocer = next(v for v in vendors if v["name"] == "سوبر ماركت الأمل")
    assert grocer["times_seen"] == 5
    assert grocer["total_spent"] == pytest.approx(728.5)
    assert grocer["category"] == "groceries", "provisional, from the model"
    assert grocer["category_confirmed"] is False, "only a person confirms"


def test_filler_notes_produce_no_vendor(client, imported):
    names = {v["name"] for v in client.get("/api/vendors").json()}
    assert not any("3352119" in n or n.strip() == "" for n in names)


def test_confirming_a_vendor_needs_a_category(client, imported):
    # A vendor the model never categorised has nothing to confirm implicitly.
    resp = client.post("/api/vendors/99999/confirm", json={})
    assert resp.status_code == 404


def test_confirming_a_vendor_settles_all_its_transactions(client, imported):
    vendors = client.get("/api/vendors").json()
    grocer = next(v for v in vendors if v["name"] == "سوبر ماركت الأمل")

    resp = client.post(f"/api/vendors/{grocer['id']}/confirm", json={"category": "groceries"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["vendor"]["category_confirmed"] is True

    # Next month's statement: the same grocer on new dates costs no model call.
    next_month = (
        "Date,Description,Debit,Credit,Currency\n"
        "2026-03-04,سوبر ماركت الأمل,120.00,,ILS\n"
        "2026-03-11,سوبر ماركت الأمل,133.00,,ILS\n"
    ).encode()
    again = client.post(
        "/api/import", files={"file": ("march.csv", next_month, "text/csv")}
    ).json()
    assert again["imported"] == 2
    assert again["from_known_vendors"] == 2
    assert again["needs_review"] == 0


def test_reimporting_the_same_statement_adds_nothing(client, imported):
    again = client.post(
        "/api/import", files={"file": ("s.csv", SAMPLE.read_bytes(), "text/csv")}
    ).json()
    assert again["imported"] == 0
    assert again["duplicates_skipped"] == 31


def test_genuinely_repeated_amounts_are_not_treated_as_duplicates(client):
    # Two real ₪3 fares on the same day with the same useless note.
    twice = (
        "Date,Description,Debit,Credit,Currency\n"
        "2026-04-01,POS 7777777,3.00,,ILS\n"
        "2026-04-01,POS 7777777,3.00,,ILS\n"
    ).encode()
    first = client.post("/api/import", files={"file": ("a.csv", twice, "text/csv")}).json()
    assert first["imported"] == 2, "identical rows in one file are both real"

    second = client.post("/api/import", files={"file": ("a.csv", twice, "text/csv")}).json()
    assert second["imported"] == 0
    assert second["duplicates_skipped"] == 2


def test_wallet_top_up_is_not_counted_as_spending(client):
    """The headline correctness case for a multi-provider household."""
    bank = b"Date,Description,Debit,Credit,Currency\n2026-05-02,TRANSFER TO JAWWALPAY,200.00,,ILS\n"
    wallet = (
        "Date,Description,Debit,Credit,Currency\n"
        "2026-05-02,TOP UP,,200.00,ILS\n"
        "2026-05-03,سوبر ماركت الأمل,145.00,,ILS\n"
    ).encode()

    before = client.get("/api/summary").json()["total_out"]

    client.post("/api/import?source=bop", files={"file": ("b.csv", bank, "text/csv")})
    result = client.post(
        "/api/import?source=jawwalpay", files={"file": ("w.csv", wallet, "text/csv")}
    ).json()
    assert result["transfers_linked"] == 1, "the note names JawwalPay, so link it"

    after = client.get("/api/summary").json()
    # Only the ₪145 of real shopping is new spending. The ₪200 moved between the
    # household's own accounts is excluded, not counted twice.
    assert after["total_out"] == pytest.approx(before + 145.00)
    assert after["internal_transfers_excluded"] >= 200.00


def test_unnamed_equal_amounts_are_queued_not_assumed(client):
    bank = b"Date,Description,Debit,Credit,Currency\n2026-06-02,POS 4412119,175.00,,ILS\n"
    wallet = b"Date,Description,Debit,Credit,Currency\n2026-06-02,MISC 9981223,,175.00,ILS\n"

    client.post("/api/import?source=bop", files={"file": ("b.csv", bank, "text/csv")})
    result = client.post(
        "/api/import?source=palpay", files={"file": ("w.csv", wallet, "text/csv")}
    ).json()
    assert result["transfers_linked"] == 0

    pending = client.get("/api/transfers/pending").json()
    match = next(p for p in pending if p["amount"] == pytest.approx(175.00))
    assert match["out_source"] == "bop"
    assert match["in_source"] == "palpay"
    assert "Did you move your own money?" in match["prompt"]

    linked = client.post(
        "/api/transfers/link", json={"out_id": match["out_id"], "in_id": match["in_id"]}
    )
    assert linked.status_code == 200
    assert linked.json()["linked"] is True


def test_unknown_source_is_rejected(client):
    resp = client.post(
        "/api/import?source=notabank",
        files={"file": ("s.csv", SAMPLE.read_bytes(), "text/csv")},
    )
    assert resp.status_code == 422
    assert "jawwalpay" in resp.json()["detail"]


def test_sources_are_seeded(client):
    slugs = {s["slug"] for s in client.get("/api/sources").json()}
    assert {"bop", "jawwalpay", "palpay", "cash", "manual"} <= slugs


def test_summary_reports_coverage(client, imported):
    body = client.get("/api/summary").json()
    rows = [
        t
        for t in client.get("/api/transactions?limit=1000").json()
        if not t["is_internal_transfer"]
    ]

    # Asserted against the rows rather than a fixed figure, so the test does not
    # care how many statements have been imported by the time it runs.
    assert body["total_in"] == pytest.approx(sum(t["amount"] for t in rows if t["amount"] > 0))
    assert body["total_out"] == pytest.approx(
        sum(abs(t["amount"]) for t in rows if t["amount"] < 0)
    )
    assert "water" in body["by_category"]
    assert 0 < body["coverage"] <= 1


def test_empty_upload_is_rejected(client):
    resp = client.post("/api/import", files={"file": ("empty.csv", b"", "text/csv")})
    assert resp.status_code == 400


def test_unparseable_upload_is_rejected(client):
    resp = client.post(
        "/api/import", files={"file": ("junk.csv", b"hello,world\n1,2\n", "text/csv")}
    )
    assert resp.status_code == 422
