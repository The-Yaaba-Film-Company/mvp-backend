import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(8)

ORIGIN = "http://localhost:3000"


class TestCorsPreflight:
    async def test_authorized_origin_preflight(self, client):
        resp = await client.options(
            "/api/auth/login",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert resp.headers["access-control-allow-credentials"] == "true"
        assert "POST" in resp.headers["access-control-allow-methods"]
        allow_headers = resp.headers["access-control-allow-headers"].lower()
        assert "content-type" in allow_headers
        assert "x-csrf-token" in allow_headers

    async def test_preflight_does_not_require_session(self, client):
        # OPTIONS must be answered by the CORS layer, not bounced as 401/403.
        resp = await client.options(
            "/api/projects/00000000-0000-0000-0000-000000000000",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "x-csrf-token",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert resp.headers["access-control-allow-credentials"] == "true"

    async def test_disallowed_origin_preflight(self, client):
        resp = await client.options(
            "/api/auth/login",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,x-csrf-token",
            },
        )
        # Starlette rejects the preflight (400) when the origin is not allowed.
        assert resp.status_code == 400
        assert "access-control-allow-origin" not in resp.headers


class TestCorsSimpleRequests:
    async def test_get_echoes_origin_and_credentials(self, client):
        resp = await client.get("/api/health", headers={"Origin": ORIGIN})
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert resp.headers["access-control-allow-credentials"] == "true"

    async def test_disallowed_origin_not_echoed(self, client):
        resp = await client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers

    async def test_no_origin_gets_no_cors_headers(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert "access-control-allow-origin" not in resp.headers


class TestCorsAuthRoundTrip:
    async def test_register_then_me_across_origin(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "cors@example.com",
                "password": "password123",
                "display_name": "Cors User",
            },
            headers={"Origin": ORIGIN},
        )
        assert resp.status_code == 201
        assert resp.headers["access-control-allow-origin"] == ORIGIN
        assert "session_id" in client.cookies
        assert "csrf_token" in client.cookies

        me = await client.get("/api/auth/me", headers={"Origin": ORIGIN})
        assert me.status_code == 200
        assert me.json()["email"] == "cors@example.com"

    async def test_mutating_request_receives_cors_headers(self, client):
        auth = await register(client, email="cors2@example.com")
        proj = await client.post(
            "/api/projects",
            json={"title": "CORS Project"},
            headers={**csrf_headers(auth["csrf"]), "Origin": ORIGIN},
        )
        assert proj.status_code == 201
        assert proj.headers["access-control-allow-origin"] == ORIGIN
        assert proj.headers["access-control-allow-credentials"] == "true"