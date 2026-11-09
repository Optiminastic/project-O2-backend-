"""The server-side password reset (python -m app.reset_password) for when nobody can sign in."""

import pytest

from app.reset_password import ResetError, ceo_accounts, reset_password


def login(client, email, password):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def test_a_reset_password_signs_in_and_reactivates_the_account(client, db, users):
    ceo = users["ceo"]
    ceo.is_active = False
    db.commit()
    reset_password(db, "  CEO@optiminastic.com ", "new-secret-123")
    db.commit()
    assert ceo.is_active is True
    assert login(client, "ceo@optiminastic.com", "new-secret-123").status_code == 200


def test_only_ceo_accounts_are_listed(db, users):
    assert [u.email for u in ceo_accounts(db)] == ["ceo@optiminastic.com"]


@pytest.mark.parametrize(("email", "password"), [("nobody@optiminastic.com", "new-secret-123"), ("ceo@optiminastic.com", "short")])
def test_a_bad_reset_changes_nothing(client, db, users, email, password):
    before = users["ceo"].hashed_password
    with pytest.raises(ResetError):
        reset_password(db, email, password)
    assert users["ceo"].hashed_password == before
