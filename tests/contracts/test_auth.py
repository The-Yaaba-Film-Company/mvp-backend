import pytest
from sqlalchemy import text

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(0)


async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_me_without_session_401(client):
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Session expired."}


async def test_register_201_snake_case_fields(client):
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "alice@example.com",
            "password": "password123",
            "display_name": "Alice",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "email", "display_name"}
    assert body["display_name"] == "Alice"


async def test_register_normalizes_email_lowercase(client):
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "Alice@Example.COM",
            "password": "password123",
            "display_name": "Alice",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "alice@example.com"


async def test_register_duplicate_email_409_case_insensitive(client):
    await register(client)
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "ALICE@example.com",
            "password": "password123",
            "display_name": "Alice2",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Email already registered."


async def test_short_password_422_string_detail(client):
    resp = await client.post(
        "/api/auth/register",
        json={"email": "bob@example.com", "password": "short", "display_name": "Bob"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], str)
    assert "password" in body["detail"]


async def test_login_sets_both_cookies(client):
    await register(client)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "alice@example.com"


async def test_login_wrong_password_401(client):
    await register(client)
    resp = await client.post(
        "/api/auth/login",
        json={"email": "alice@example.com", "password": "wrongpass"},
    )
    assert resp.status_code == 401


async def test_session_cookie_httponly_and_csrf_cookie_readable(client):
    resp = await client.post(
        "/api/auth/register",
        json={
            "email": "carol@example.com",
            "password": "password123",
            "display_name": "Carol",
        },
    )
    set_cookies = {c.split("=")[0]: c for c in resp.headers.get_list("set-cookie")}
    session_cookie = set_cookies["session_id"]
    csrf_cookie = set_cookies["csrf_token"]
    assert "HttpOnly" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Path=/" in session_cookie
    assert "Path=/" in csrf_cookie
    assert resp.json()["id"]


async def test_logout_revokes_session_then_me_401(client):
    auth = await register(client)
    resp = await client.post("/api/auth/logout", headers=csrf_headers(auth["csrf"]))
    assert resp.status_code == 204
    me = await client.get("/api/auth/me")
    assert me.status_code == 401


async def test_mutating_without_csrf_403(client):
    await register(client)
    resp = await client.post("/api/projects", json={"title": "P"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF validation failed."


async def test_mutating_with_wrong_csrf_403(client):
    await register(client)
    resp = await client.post(
        "/api/projects",
        json={"title": "P"},
        headers=csrf_headers("not-the-token"),
    )
    assert resp.status_code == 403


async def test_expired_session_401(client, db_engine):
    auth = await register(client)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE sessions SET expires_at = '2020-01-01' WHERE user_id = :uid"),
            {"uid": auth["user"]["id"]},
        )
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401