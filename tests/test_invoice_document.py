"""The printed invoice: amount in words, place of supply and the staff document endpoint."""

import pytest

from app.services.invoice_document import rupees_in_words
from tests.conftest import auth_header, project_id_for


@pytest.mark.parametrize("amount, words", [
    (0, "Zero Only"),
    (7, "Seven Only"),
    (118000, "One Lakh Eighteen Thousand Only"),
    (1180.5, "One Thousand One Hundred Eighty and Fifty Paise Only"),
    (99_99_999, "Ninety Nine Lakh Ninety Nine Thousand Nine Hundred Ninety Nine Only"),
    (12_34_56_789.01, "Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven Hundred Eighty Nine and One Paise Only"),
    (150_00_00_000, "One Hundred Fifty Crore Only"),
])
def test_rupees_in_words_uses_indian_grouping(amount, words):
    assert rupees_in_words(amount) == words


def new_invoice(client, user, client_row, **fields):
    body = {"project_id": project_id_for(client_row), "taxable_value": 100000, **fields}
    res = client.post("/api/invoices", json=body, headers=auth_header(user))
    assert res.status_code == 201, res.text
    return res.json()


def document(client, user, invoice_id):
    return client.get(f"/api/invoices/{invoice_id}/document", headers=auth_header(user))


def test_document_has_company_client_totals_and_words(client, users, client_row):
    inv = new_invoice(client, users["exec"], client_row)
    res = document(client, users["exec"], inv["id"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["invoice"]["total_amount"] == 118000
    assert body["amount_in_words"] == "One Lakh Eighteen Thousand Only"
    assert body["company"]["gstin"] and "bank_branch" in body["company"]
    assert "internal_remarks" not in body["invoice"]  # same client-safe view as the link page


def test_place_of_supply_comes_from_the_client_gstin(client, users, client_row, db):
    client_row.gst_number = "29AAGCB7383J1Z4"  # Karnataka
    db.commit()
    inv = new_invoice(client, users["exec"], client_row, is_interstate=True)
    assert document(client, users["exec"], inv["id"]).json()["place_of_supply"] == "Karnataka"


def test_intra_state_supply_without_gstin_is_our_state(client, users, client_row, db):
    client_row.gst_number = None
    db.commit()
    inv = new_invoice(client, users["exec"], client_row)
    from app.config import settings
    assert document(client, users["exec"], inv["id"]).json()["place_of_supply"] == settings.company_state


def test_document_needs_login_and_an_existing_invoice(client, users):
    assert client.get("/api/invoices/1/document").status_code == 401
    assert document(client, users["exec"], 999999).status_code == 404
