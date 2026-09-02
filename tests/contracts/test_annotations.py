import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(2)

def make_scene_content(character_id: str) -> dict:
    """Create valid scene content with a real character entity ID."""
    return {
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
                "text": "John enters the room with a pistol."
            },
            {
                "type": "character",
                "attrs": {
                    "characterId": character_id,
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


async def _setup_scene_with_entity(client):
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

    # Create a character entity (referenced by scene content) and a prop entity
    char_resp = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "character", "canonical_name": "JOHN"},
        headers=csrf_headers(auth["csrf"]),
    )
    char_id = char_resp.json()["id"]

    entity_resp = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "prop", "canonical_name": "PISTOL"},
        headers=csrf_headers(auth["csrf"]),
    )
    entity_id = entity_resp.json()["id"]

    # Create a scene
    scene_resp = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": make_scene_content(char_id)},
        headers=csrf_headers(auth["csrf"]),
    )
    scene_id = scene_resp.json()["id"]

    return auth, project_id, screenplay_id, scene_id, entity_id


class TestAnnotationList:
    async def test_list_annotations_envelope_items(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)

        # Create an annotation
        await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "n1", "start_offset": 25, "end_offset": 31, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )

        resp = await client.get(f"/api/scenes/{scene_id}/annotations")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) == 1
        assert body["items"][0]["entity_id"] == entity_id


class TestAnnotationCreate:
    async def test_create_annotation_201(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)
        resp = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "n1", "start_offset": 25, "end_offset": 31, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201
        assert resp.json()["entity_id"] == entity_id
        assert resp.json()["source"] == "manual"
        assert resp.json()["node_id"] == "n1"

    async def test_create_annotation_invalid_node_422(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)
        resp = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "nonexistent", "start_offset": 0, "end_offset": 5, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422

    async def test_create_annotation_atom_node_422(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)
        # Try to annotate sceneHeading (atom node)
        resp = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "sceneHeading_node", "start_offset": 0, "end_offset": 5, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422

    async def test_create_annotation_offsets_out_of_bounds_422(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)
        resp = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "n1", "start_offset": 100, "end_offset": 200, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422

    async def test_create_annotation_wrong_project_entity_404(self, client):
        auth, _project_id, _screenplay_id, scene_id, _entity_id = await _setup_scene_with_entity(client)
        # Create another project and entity
        other_proj = await client.post(
            "/api/projects", json={"title": "Other Project"}, headers=csrf_headers(auth["csrf"])
        )
        other_project_id = other_proj.json()["id"]
        other_entity = await client.post(
            f"/api/projects/{other_project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "KNIFE"},
            headers=csrf_headers(auth["csrf"]),
        )
        other_entity_id = other_entity.json()["id"]

        resp = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "n1", "start_offset": 0, "end_offset": 5, "entity_id": other_entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 404


class TestAnnotationDelete:
    async def test_delete_annotation(self, client):
        auth, _project_id, _screenplay_id, scene_id, entity_id = await _setup_scene_with_entity(client)
        created = await client.post(
            f"/api/scenes/{scene_id}/annotations",
            json={"node_id": "n1", "start_offset": 25, "end_offset": 31, "entity_id": entity_id},
            headers=csrf_headers(auth["csrf"]),
        )
        ann_id = created.json()["id"]

        resp = await client.delete(
            f"/api/annotations/{ann_id}", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 204

        list_resp = await client.get(f"/api/scenes/{scene_id}/annotations")
        assert list_resp.json()["items"] == []