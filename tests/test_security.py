from uuid import uuid4

from app.security import (
    csrf_token_value,
    hash_password,
    verify_csrf,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("password123")
    assert hashed != "password123"
    assert verify_password(hashed, "password123") is True
    assert verify_password(hashed, "wrong") is False


def test_verify_password_invalid_hash_returns_false():
    assert verify_password("not-a-valid-hash", "password123") is False


def test_csrf_token_deterministic_and_bound_to_session():
    secret = "csrfs3cr3t"
    sid_a = uuid4()
    sid_b = uuid4()
    token = csrf_token_value(secret, sid_a)
    assert csrf_token_value(secret, sid_a) == token
    assert csrf_token_value(secret, sid_b) != token
    assert csrf_token_value("other-secret", sid_a) != token
    assert verify_csrf(secret, sid_a, token) is True
    assert verify_csrf(secret, sid_a, "forged") is False