from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

SAMPLE = Path(__file__).parent.parent / "data" / "sample_statement.csv"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


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
    body = resp.json()
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


def test_summary_reports_coverage(client, imported):
    body = client.get("/api/summary").json()
    assert body["total_out"] > 0
    assert body["total_in"] == 2400.0
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
