import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(3)


async def _setup_project_with_data(client):
    auth = await register(client)
    proj = await client.post(
        "/api/projects", json={"title": "Test Project"}, headers=csrf_headers(auth["csrf"])
    )
    project_id = proj.json()["id"]
    sp = await client.post(
        f"/api/projects/{project_id}/screenplays",
        json={"title": "Test Screenplay"},
        headers=csrf_headers(auth["csrf"]),
    )
    screenplay_id = sp.json()["id"]

    # Create entities
    char = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "character", "canonical_name": "JOHN"},
        headers=csrf_headers(auth["csrf"]),
    )
    char_id = char.json()["id"]

    prop = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "prop", "canonical_name": "PISTOL"},
        headers=csrf_headers(auth["csrf"]),
    )
    prop_id = prop.json()["id"]

    loc = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "location", "canonical_name": "POLICE STATION"},
        headers=csrf_headers(auth["csrf"]),
    )
    loc_id = loc.json()["id"]

    # Create scene with correct character ID
    scene_content = {
        "type": "doc",
        "content": [
            {
                "type": "sceneHeading",
                "attrs": {
                    "intExt": "INT",
                    "location": "POLICE STATION",
                    "timeOfDay": "DAY"
                }
            },
            {
                "type": "action",
                "attrs": {"id": "n1"},
                "text": "John enters with a pistol."
            },
            {
                "type": "character",
                "attrs": {
                    "characterId": char_id,
                    "displayName": "JOHN"
                }
            },
            {
                "type": "dialogue",
                "attrs": {"id": "n2"},
                "text": "Hello there."
            }
        ]
    }
    scene = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": scene_content},
        headers=csrf_headers(auth["csrf"]),
    )
    scene_id = scene.json()["id"]

    # Create annotation for prop
    await client.post(
        f"/api/scenes/{scene_id}/annotations",
        json={"node_id": "n1", "start_offset": 20, "end_offset": 26, "entity_id": prop_id},
        headers=csrf_headers(auth["csrf"]),
    )

    return auth, project_id, screenplay_id, scene_id, char_id, prop_id, loc_id


class TestCharactersReport:
    async def test_characters_report_bare_array(self, client):
        _auth, project_id, _screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        resp = await client.get(f"/api/projects/{project_id}/reports/characters")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        char = body[0]
        assert set(char.keys()) == {"id", "canonical_name", "aliases", "scene_ids", "dialogue_count"}
        assert char["canonical_name"] == "JOHN"
        assert char["dialogue_count"] == 1
        assert scene_id in char["scene_ids"]


class TestLocationsReport:
    async def test_locations_report_bare_array(self, client, db_engine):
        _auth, project_id, _screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        resp = await client.get(f"/api/projects/{project_id}/reports/locations")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        loc = body[0]
        assert set(loc.keys()) == {"id", "canonical_name", "aliases", "scene_ids"}
        assert loc["canonical_name"] == "POLICE STATION"
        assert scene_id in loc["scene_ids"]


class TestEntitiesReport:
    async def test_entities_report_bare_array(self, client):
        _auth, project_id, _screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        resp = await client.get(f"/api/projects/{project_id}/reports/entities")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        ent = body[0]
        assert set(ent.keys()) == {"id", "entity_type", "canonical_name", "aliases", "scene_ids", "occurrence_count"}
        # entity_type is returned as string for API compatibility
        assert ent["entity_type"] == "prop"
        assert ent["canonical_name"] == "PISTOL"
        assert ent["occurrence_count"] == 1
        assert scene_id in ent["scene_ids"]


class TestRuntimeReport:
    async def test_runtime_report(self, client):
        _auth, _project_id, screenplay_id, _scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        resp = await client.get(f"/api/screenplays/{screenplay_id}/reports/runtime")
        assert resp.status_code == 200
        body = resp.json()
        assert "runtime_minutes" in body
        assert isinstance(body["runtime_minutes"], int)
        assert body["runtime_minutes"] >= 0


class TestPaginationReport:
    async def test_pagination_report_empty(self, client):
        _auth, _project_id, screenplay_id, _scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        resp = await client.get(f"/api/screenplays/{screenplay_id}/reports/pagination")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {"page_count", "runtime_minutes", "scene_metrics"}
        assert body["page_count"] == 0
        assert body["scene_metrics"] == []

    async def test_update_pagination_report(self, client):
        auth, _project_id, screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        payload = {
            "page_count": 10,
            "runtime_minutes": 10,
            "scene_metrics": [
                {"scene_id": scene_id, "start_page": 1, "end_page": 10, "page_length": 2.5}
            ]
        }
        resp = await client.patch(
            f"/api/screenplays/{screenplay_id}/reports/pagination",
            json=payload,
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["page_count"] == 10

        # Verify it was saved
        get_resp = await client.get(f"/api/screenplays/{screenplay_id}/reports/pagination")
        assert get_resp.status_code == 200
        assert get_resp.json()["page_count"] == 10
        assert len(get_resp.json()["scene_metrics"]) == 1

    async def test_page_count_computed_from_end_page(self, client):
        auth, _project_id, screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)

        payload = {
            "page_count": 3,
            "runtime_minutes": 1,
            "scene_metrics": [
                {"scene_id": scene_id, "start_page": 1, "end_page": 2, "page_length": 1.0}
            ],
        }
        await client.patch(
            f"/api/screenplays/{screenplay_id}/reports/pagination",
            json=payload,
            headers=csrf_headers(auth["csrf"]),
        )

        # The cache is non-authoritative: page_count is recomputed from end_page.
        body = (await client.get(f"/api/screenplays/{screenplay_id}/reports/pagination")).json()
        assert body["page_count"] == 2
        assert body["scene_metrics"] == [
            {"scene_id": scene_id, "start_page": 1, "end_page": 2, "page_length": 1.0}
        ]

    async def test_patch_requires_csrf(self, client):
        _auth, _project_id, screenplay_id, scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)
        payload = {
            "page_count": 1,
            "runtime_minutes": 1,
            "scene_metrics": [
                {"scene_id": scene_id, "start_page": 1, "end_page": 1, "page_length": 0.7}
            ],
        }
        resp = await client.patch(
            f"/api/screenplays/{screenplay_id}/reports/pagination", json=payload
        )
        assert resp.status_code == 403

    async def test_requires_session_401(self, client):
        _auth, _project_id, screenplay_id, _scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)
        client.cookies.clear()
        resp = await client.get(f"/api/screenplays/{screenplay_id}/reports/pagination")
        assert resp.status_code == 401

    async def test_unknown_screenplay_404(self, client, db_engine):
        _auth, _project_id, _screenplay_id, _scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)
        resp = await client.get(
            "/api/screenplays/00000000-0000-0000-0000-000000000000/reports/pagination"
        )
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Screenplay not found."}

    async def test_viewer_cannot_write(self, client, make_client):
        auth, project_id, screenplay_id, _scene_id, _char_id, _prop_id, _loc_id = await _setup_project_with_data(client)
        bob_client = make_client()
        bob = await register(bob_client, email="bob@example.com", display_name="Bob")
        await client.post(
            f"/api/projects/{project_id}/members",
            json={"user_id": bob["user"]["id"], "role": "viewer"},
            headers=csrf_headers(auth["csrf"]),
        )
        payload = {
            "page_count": 1,
            "runtime_minutes": 1,
            "scene_metrics": [],
        }
        resp = await bob_client.patch(
            f"/api/screenplays/{screenplay_id}/reports/pagination",
            json=payload,
            headers=csrf_headers(bob["csrf"]),
        )
        assert resp.status_code == 403

        read = await bob_client.get(f"/api/screenplays/{screenplay_id}/reports/pagination")
        assert read.status_code == 200