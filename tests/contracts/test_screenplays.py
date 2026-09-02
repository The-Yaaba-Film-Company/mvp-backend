from uuid import uuid4

import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(1)

SCREENPLAY_FIELDS = {
    "id",
    "project_id",
    "title",
    "locked_at",
    "created_at",
    "updated_at",
}


async def _create_project_and_get_csrf(client):
    auth = await register(client)
    resp = await client.post(
        "/api/projects", json={"title": "Test Project"}, headers=csrf_headers(auth["csrf"])
    )
    assert resp.status_code == 201
    return auth, resp.json()["id"]


async def _create_screenplay(client, project_id, csrf, title="Test Screenplay"):
    resp = await client.post(
        f"/api/projects/{project_id}/screenplays",
        json={"title": title},
        headers=csrf_headers(csrf),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestScreenplayList:
    async def test_list_screenplays_bare_array(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        await _create_screenplay(client, project_id, auth["csrf"], "Screenplay A")
        await _create_screenplay(client, project_id, auth["csrf"], "Screenplay B")

        resp = await client.get(f"/api/projects/{project_id}/screenplays")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        assert {s["title"] for s in body} == {"Screenplay A", "Screenplay B"}
        for s in body:
            assert set(s.keys()) == SCREENPLAY_FIELDS


class TestScreenplayCreate:
    async def test_create_screenplay_201(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])
        assert set(created.keys()) == SCREENPLAY_FIELDS
        assert created["title"] == "Test Screenplay"
        assert created["project_id"] == project_id
        assert created["locked_at"] is None

    async def test_create_screenplay_requires_auth(self, client):
        resp = await client.post("/api/projects/00000000-0000-0000-0000-000000000000/screenplays", json={"title": "X"})
        assert resp.status_code == 401

    async def test_create_screenplay_requires_csrf(self, client):
        _auth, project_id = await _create_project_and_get_csrf(client)
        resp = await client.post(f"/api/projects/{project_id}/screenplays", json={"title": "No CSRF"})
        assert resp.status_code == 403

    async def test_create_screenplay_viewer_forbidden(self, client, make_client):
        alice = await register(client)
        created = await client.post(
            "/api/projects", json={"title": "P"}, headers=csrf_headers(alice["csrf"])
        )
        project_id = created.json()["id"]

        bob_client = make_client()
        await register(bob_client, email="bob@example.com", display_name="Bob")
        await client.post(
            f"/api/projects/{project_id}/members",
            json={"user_id": bob_client.cookies.get("user_id") or "00000000-0000-0000-0000-000000000000", "role": "viewer"},
            headers=csrf_headers(alice["csrf"]),
        )
        # Actually need to get bob's user id properly
        bob_auth = await register(bob_client, email="bob2@example.com", display_name="Bob2")
        await client.post(
            f"/api/projects/{project_id}/members",
            json={"user_id": bob_auth["user"]["id"], "role": "viewer"},
            headers=csrf_headers(alice["csrf"]),
        )
        resp = await bob_client.post(
            f"/api/projects/{project_id}/screenplays",
            json={"title": "X"},
            headers=csrf_headers(bob_auth["csrf"]),
        )
        assert resp.status_code == 403


class TestScreenplayGet:
    async def test_get_screenplay(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])

        resp = await client.get(f"/api/screenplays/{created['id']}", headers=csrf_headers(auth["csrf"]))
        assert resp.status_code == 200
        assert set(resp.json().keys()) == SCREENPLAY_FIELDS
        assert resp.json()["title"] == "Test Screenplay"

    async def test_get_screenplay_not_found(self, client):
        auth = await register(client)
        resp = await client.get(f"/api/screenplays/{uuid4()}", headers=csrf_headers(auth["csrf"]))
        assert resp.status_code == 404


class TestScreenplayUpdate:
    async def test_update_screenplay(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])

        resp = await client.patch(
            f"/api/screenplays/{created['id']}",
            json={"title": "Updated Title"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"

    async def test_update_locked_screenplay_409(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])
        await client.post(f"/api/screenplays/{created['id']}/lock", headers=csrf_headers(auth["csrf"]))

        resp = await client.patch(
            f"/api/screenplays/{created['id']}",
            json={"title": "Updated Title"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 409


class TestScreenplayLock:
    async def test_lock_screenplay(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])

        resp = await client.post(f"/api/screenplays/{created['id']}/lock", headers=csrf_headers(auth["csrf"]))
        assert resp.status_code == 200
        assert resp.json()["locked_at"] is not None

    async def test_lock_idempotent(self, client):
        auth, project_id = await _create_project_and_get_csrf(client)
        created = await _create_screenplay(client, project_id, auth["csrf"])

        await client.post(f"/api/screenplays/{created['id']}/lock", headers=csrf_headers(auth["csrf"]))
        resp2 = await client.post(f"/api/screenplays/{created['id']}/lock", headers=csrf_headers(auth["csrf"]))
        assert resp2.status_code == 200

    async def test_lock_requires_editor(self, client, make_client):
        alice = await register(client)
        created = await client.post(
            "/api/projects", json={"title": "P"}, headers=csrf_headers(alice["csrf"])
        )
        project_id = created.json()["id"]
        sp = await _create_screenplay(client, project_id, alice["csrf"])

        bob_client = make_client()
        bob_auth = await register(bob_client, email="bob@example.com", display_name="Bob")
        await client.post(
            f"/api/projects/{project_id}/members",
            json={"user_id": bob_auth["user"]["id"], "role": "viewer"},
            headers=csrf_headers(alice["csrf"]),
        )
        resp = await bob_client.post(
            f"/api/screenplays/{sp['id']}/lock",
            headers=csrf_headers(bob_auth["csrf"]),
        )
        assert resp.status_code == 403