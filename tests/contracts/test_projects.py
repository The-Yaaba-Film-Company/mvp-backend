import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(0)

PROJECT_FIELDS = {
    "id",
    "title",
    "description",
    "owner_id",
    "role",
    "created_at",
    "updated_at",
}


async def _create_project(client, token, title="Film"):
    resp = await client.post(
        "/api/projects", json={"title": title}, headers=csrf_headers(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_project_201_role_owner(client):
    auth = await register(client)
    created = await _create_project(client, auth["csrf"])
    assert set(created) == PROJECT_FIELDS
    assert created["title"] == "Film"
    assert created["description"] is None
    assert created["role"] == "owner"
    assert created["owner_id"] == auth["user"]["id"]


async def test_list_projects_is_bare_array(client):
    auth = await register(client)
    await _create_project(client, auth["csrf"], title="Film A")
    await _create_project(client, auth["csrf"], title="Film B")
    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert {p["title"] for p in body} == {"Film A", "Film B"}
    assert all(p["role"] == "owner" for p in body)


async def test_get_project_includes_role(client):
    auth = await register(client)
    created = await _create_project(client, auth["csrf"])
    resp = await client.get(f"/api/projects/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["role"] == "owner"


async def test_patch_project_updates_and_keeps_role(client):
    auth = await register(client)
    created = await _create_project(client, auth["csrf"])
    resp = await client.patch(
        f"/api/projects/{created['id']}",
        json={"title": "Renamed"},
        headers=csrf_headers(auth["csrf"]),
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "Renamed"
    assert resp.json()["role"] == "owner"


async def test_patch_clears_description_when_null(client):
    auth = await register(client)
    created = await _create_project(client, auth["csrf"])
    resp = await client.patch(
        f"/api/projects/{created['id']}",
        json={"description": None},
        headers=csrf_headers(auth["csrf"]),
    )
    assert resp.status_code == 200
    assert resp.json()["description"] is None


async def test_delete_project_204(client):
    auth = await register(client)
    created = await _create_project(client, auth["csrf"])
    resp = await client.delete(
        f"/api/projects/{created['id']}", headers=csrf_headers(auth["csrf"])
    )
    assert resp.status_code == 204
    assert (await client.get("/api/projects")).json() == []


async def test_non_member_get_403(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    await register(bob_client, email="bob@example.com", display_name="Bob")
    resp = await bob_client.get(f"/api/projects/{created['id']}")
    assert resp.status_code == 403


async def test_viewer_cannot_patch_403(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    bob = await register(bob_client, email="bob@example.com", display_name="Bob")
    added = await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob["user"]["id"], "role": "viewer"},
        headers=csrf_headers(alice["csrf"]),
    )
    assert added.status_code == 201
    resp = await bob_client.patch(
        f"/api/projects/{created['id']}",
        json={"title": "X"},
        headers=csrf_headers(bob["csrf"]),
    )
    assert resp.status_code == 403


async def test_editor_can_patch_but_not_delete(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    bob = await register(bob_client, email="bob@example.com", display_name="Bob")
    await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob["user"]["id"], "role": "editor"},
        headers=csrf_headers(alice["csrf"]),
    )
    patched = await bob_client.patch(
        f"/api/projects/{created['id']}",
        json={"title": "by editor"},
        headers=csrf_headers(bob["csrf"]),
    )
    assert patched.status_code == 200
    deleted = await bob_client.delete(
        f"/api/projects/{created['id']}", headers=csrf_headers(bob["csrf"])
    )
    assert deleted.status_code == 403
    assert patched.json()["role"] == "editor"


async def test_member_can_list_own_projects_with_role(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    bob = await register(bob_client, email="bob@example.com", display_name="Bob")
    await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob["user"]["id"], "role": "editor"},
        headers=csrf_headers(alice["csrf"]),
    )
    listed = await bob_client.get("/api/projects")
    assert listed.status_code == 200
    project = listed.json()[0]
    assert project["title"] == "Film"
    assert project["role"] == "editor"


async def test_add_and_remove_member(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    bob = await register(bob_client, email="bob@example.com", display_name="Bob")
    resp = await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob["user"]["id"], "role": "editor"},
        headers=csrf_headers(alice["csrf"]),
    )
    assert resp.status_code == 201
    member = resp.json()
    assert set(member) == {"id", "email", "display_name", "role", "added_at"}
    assert member["email"] == "bob@example.com"
    assert member["role"] == "editor"

    members = await client.get(f"/api/projects/{created['id']}/members")
    assert [m["id"] for m in members.json()] == [alice["user"]["id"], bob["user"]["id"]]

    removed = await client.delete(
        f"/api/projects/{created['id']}/members/{bob['user']['id']}",
        headers=csrf_headers(alice["csrf"]),
    )
    assert removed.status_code == 204
    remaining = (await client.get(f"/api/projects/{created['id']}/members")).json()
    assert [m["id"] for m in remaining] == [alice["user"]["id"]]


async def test_add_member_404_unknown_user(client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    resp = await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": "00000000-0000-0000-0000-000000000000"},
        headers=csrf_headers(alice["csrf"]),
    )
    assert resp.status_code == 404


async def test_add_member_409_duplicate(client, make_client):
    alice = await register(client)
    created = await _create_project(client, alice["csrf"])
    bob_client = make_client()
    bob = await register(bob_client, email="bob@example.com", display_name="Bob")
    bob_id = bob["user"]["id"]
    await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob_id, "role": "editor"},
        headers=csrf_headers(alice["csrf"]),
    )
    resp = await client.post(
        f"/api/projects/{created['id']}/members",
        json={"user_id": bob_id, "role": "viewer"},
        headers=csrf_headers(alice["csrf"]),
    )
    assert resp.status_code == 409


async def test_create_project_blank_title_422(client):
    auth = await register(client)
    resp = await client.post(
        "/api/projects",
        json={"title": "   "},
        headers=csrf_headers(auth["csrf"]),
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)