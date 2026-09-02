
import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(2)

ENTITY_FIELDS = {
    "id",
    "project_id",
    "entity_type",
    "canonical_name",
    "aliases",
    "attributes",
    "created_at",
    "updated_at",
}


async def _setup_project(client):
    auth = await register(client)
    proj = await client.post(
        "/api/projects", json={"title": "Test Project"}, headers=csrf_headers(auth["csrf"])
    )
    project_id = proj.json()["id"]
    return auth, project_id


class TestEntityList:
    async def test_list_entities_bare_array(self, client):
        auth, project_id = await _setup_project(client)
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "KNIFE"},
            headers=csrf_headers(auth["csrf"]),
        )

        resp = await client.get(f"/api/projects/{project_id}/entities?type=prop")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 2
        assert {e["canonical_name"] for e in body} == {"PISTOL", "KNIFE"}
        for e in body:
            assert set(e.keys()) == ENTITY_FIELDS
            # entity_type is returned as string for API compatibility
            assert e["entity_type"] == "prop"

    async def test_list_entities_filters_by_type(self, client):
        auth, project_id = await _setup_project(client)
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "character", "canonical_name": "JOHN"},
            headers=csrf_headers(auth["csrf"]),
        )
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )

        resp = await client.get(f"/api/projects/{project_id}/entities?type=character")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["canonical_name"] == "JOHN"

    async def test_list_entities_fuzzy_search(self, client):
        auth, project_id = await _setup_project(client)
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )

        resp = await client.get(f"/api/projects/{project_id}/entities?type=prop&q=pist")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["canonical_name"] == "PISTOL"


class TestEntityCreate:
    async def test_create_entity_201(self, client):
        auth, project_id = await _setup_project(client)
        resp = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201
        assert set(resp.json().keys()) == ENTITY_FIELDS
        assert resp.json()["canonical_name"] == "PISTOL"
        # entity_type is returned as string for API compatibility
        assert resp.json()["entity_type"] == "prop"

    async def test_create_entity_duplicate_409(self, client):
        auth, project_id = await _setup_project(client)
        await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        resp = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "pistol"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 409

    async def test_create_entity_invalid_type_422(self, client):
        auth, project_id = await _setup_project(client)
        resp = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "invalid", "canonical_name": "X"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422


class TestEntityGet:
    async def test_get_entity(self, client):
        auth, project_id = await _setup_project(client)
        created = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        entity_id = created.json()["id"]

        resp = await client.get(f"/api/entities/{entity_id}")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == ENTITY_FIELDS
        assert resp.json()["id"] == entity_id


class TestEntityUpdate:
    async def test_update_entity_rename(self, client):
        auth, project_id = await _setup_project(client)
        created = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        entity_id = created.json()["id"]

        resp = await client.patch(
            f"/api/entities/{entity_id}",
            json={"canonical_name": "REVOLVER"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["canonical_name"] == "REVOLVER"


class TestEntityMerge:
    async def test_merge_entities(self, client):
        auth, project_id = await _setup_project(client)
        source = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        target = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "REVOLVER"},
            headers=csrf_headers(auth["csrf"]),
        )

        resp = await client.post(
            "/api/entities/merge",
            json={"source_id": source.json()["id"], "target_id": target.json()["id"]},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["canonical_name"] == "REVOLVER"

        # Source should be gone
        resp = await client.get(f"/api/entities/{source.json()['id']}")
        assert resp.status_code == 404


async def _setup_screenplay(client, auth, project_id):
    sp = await client.post(
        f"/api/projects/{project_id}/screenplays",
        json={"title": "Rename Screenplay"},
        headers=csrf_headers(auth["csrf"]),
    )
    return sp.json()["id"]


async def _create_scene(client, auth, screenplay_id, char_id, display_name, location):
    content = {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": location, "timeOfDay": "DAY"}},
            {"type": "action", "attrs": {"id": "a1"}, "text": "Beats."},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": display_name}},
            {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Hi."},
        ],
    }
    resp = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": content},
        headers=csrf_headers(auth["csrf"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRenameEverywhere:
    async def test_rename_character_updates_content(self, client):
        auth, project_id = await _setup_project(client)
        screenplay_id = await _setup_screenplay(client, auth, project_id)
        john = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "character", "canonical_name": "JOHN"},
            headers=csrf_headers(auth["csrf"]),
        )
        char_id = john.json()["id"]
        scene = await _create_scene(client, auth, screenplay_id, char_id, "JOHN", "ROOM")

        resp = await client.patch(
            f"/api/entities/{char_id}",
            json={"canonical_name": "JACK"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["canonical_name"] == "JACK"
        assert "JOHN" in resp.json()["aliases"]

        scene_resp = await client.get(f"/api/scenes/{scene['id']}")
        nodes = scene_resp.json()["content"]["content"]
        char_node = next(n for n in nodes if n["type"] == "character")
        assert char_node["attrs"]["displayName"] == "JACK"
        assert char_node["attrs"]["characterId"] == char_id

    async def test_rename_location_updates_heading(self, client):
        auth, project_id = await _setup_project(client)
        screenplay_id = await _setup_screenplay(client, auth, project_id)
        # Scene heading auto-creates the ALLEY location entity
        scene = await _create_scene(client, auth, screenplay_id, "", "NOBODY", "ALLEY")
        loc_id = scene["location_entity_id"]
        assert loc_id is not None

        resp = await client.patch(
            f"/api/entities/{loc_id}",
            json={"canonical_name": "BACK ALLEY"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200

        scene_resp = await client.get(f"/api/scenes/{scene['id']}")
        nodes = scene_resp.json()["content"]["content"]
        heading = next(n for n in nodes if n["type"] == "sceneHeading")
        assert heading["attrs"]["location"] == "BACK ALLEY"
        assert scene_resp.json()["location_entity_id"] == loc_id

    async def test_rename_prop_does_not_touch_content(self, client):
        auth, project_id = await _setup_project(client)
        screenplay_id = await _setup_screenplay(client, auth, project_id)
        prop = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        prop_id = prop.json()["id"]
        scene = await _create_scene(client, auth, screenplay_id, "", "NOBODY", "ROOM")

        resp = await client.patch(
            f"/api/entities/{prop_id}",
            json={"canonical_name": "REVOLVER"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200

        scene_resp = await client.get(f"/api/scenes/{scene['id']}")
        assert scene_resp.json()["content"] == scene["content"]


class TestMergeRefinements:
    async def test_merge_character_remaps_content(self, client):
        auth, project_id = await _setup_project(client)
        screenplay_id = await _setup_screenplay(client, auth, project_id)
        john = (
            await client.post(
                f"/api/projects/{project_id}/entities",
                json={"entity_type": "character", "canonical_name": "JOHN"},
                headers=csrf_headers(auth["csrf"]),
            )
        ).json()["id"]
        jenny = (
            await client.post(
                f"/api/projects/{project_id}/entities",
                json={"entity_type": "character", "canonical_name": "JENNY"},
                headers=csrf_headers(auth["csrf"]),
            )
        ).json()["id"]
        scene1 = await _create_scene(client, auth, screenplay_id, john, "JOHN", "ROOM")
        scene2 = await _create_scene(client, auth, screenplay_id, jenny, "JENNY", "KITCHEN")

        resp = await client.post(
            "/api/entities/merge",
            json={"source_id": john, "target_id": jenny},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200

        for scene_id, display, location in ((scene1["id"], "JENNY", "ROOM"), (scene2["id"], "JENNY", "KITCHEN")):
            scene_resp = await client.get(f"/api/scenes/{scene_id}")
            nodes = scene_resp.json()["content"]["content"]
            char_node = next(n for n in nodes if n["type"] == "character")
            assert char_node["attrs"]["characterId"] == jenny
            assert char_node["attrs"]["displayName"] == display

        # Both scenes now attribute the target character
        report = await client.get(f"/api/projects/{project_id}/reports/characters")
        target = next(e for e in report.json() if e["id"] == jenny)
        assert set(target["scene_ids"]) == {scene1["id"], scene2["id"]}

        resp = await client.get(f"/api/entities/{john}")
        assert resp.status_code == 404

    async def test_merge_location_reassigns_heading(self, client):
        auth, project_id = await _setup_project(client)
        screenplay_id = await _setup_screenplay(client, auth, project_id)
        scene = await _create_scene(client, auth, screenplay_id, "", "NOBODY", "ALLEY")
        source_id = scene["location_entity_id"]
        target = (
            await client.post(
                f"/api/projects/{project_id}/entities",
                json={"entity_type": "location", "canonical_name": "BACK ALLEY"},
                headers=csrf_headers(auth["csrf"]),
            )
        ).json()

        resp = await client.post(
            "/api/entities/merge",
            json={"source_id": source_id, "target_id": target["id"]},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200

        scene_resp = await client.get(f"/api/scenes/{scene['id']}")
        assert scene_resp.json()["location_entity_id"] == target["id"]
        nodes = scene_resp.json()["content"]["content"]
        heading = next(n for n in nodes if n["type"] == "sceneHeading")
        assert heading["attrs"]["location"] == "BACK ALLEY"

    async def test_merge_different_entity_types_400(self, client):
        auth, project_id = await _setup_project(client)
        john = (
            await client.post(
                f"/api/projects/{project_id}/entities",
                json={"entity_type": "character", "canonical_name": "JOHN"},
                headers=csrf_headers(auth["csrf"]),
            )
        ).json()
        prop = (
            await client.post(
                f"/api/projects/{project_id}/entities",
                json={"entity_type": "prop", "canonical_name": "PISTOL"},
                headers=csrf_headers(auth["csrf"]),
            )
        ).json()

        resp = await client.post(
            "/api/entities/merge",
            json={"source_id": john["id"], "target_id": prop["id"]},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 400

        # Both untouched
        assert (await client.get(f"/api/entities/{john['id']}")).status_code == 200
        assert (await client.get(f"/api/entities/{prop['id']}")).status_code == 200

    async def test_merge_requires_csrf(self, client):
        await _setup_project(client)
        resp = await client.post(
            "/api/entities/merge",
            json={"source_id": "00000000-0000-0000-0000-000000000000", "target_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert resp.status_code == 403