
import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(1)

SCENE_FIELDS = {
    "id",
    "screenplay_id",
    "order_key",
    "number",
    "number_suffix",
    "locked",
    "int_ext",
    "location_entity_id",
    "time_of_day",
    "heading_modifier",
    "content",
    "content_hash",
    "created_at",
    "updated_at",
}


def make_scene_content(character_id: str) -> dict:
    """Create valid scene content with a real character ID."""
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
                "text": "John enters the room."
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


# The editor sends its ProseMirror JSON verbatim: text blocks carry their text
# as nested `content: [{"type": "text", "text": ...}]` nodes, not a `text`
# string. The PATCH/POST contract must accept that shape (SPEC §2).
TIPTAP_SHAPE_CONTENT = {
    "type": "doc",
    "content": [
        {
            "type": "sceneHeading",
            "attrs": {"intExt": "INT", "location": "ALL-EE", "timeOfDay": "DAY"},
        },
        {
            "type": "action",
            "attrs": {"id": "n-tiptap-1"},
            "content": [{"type": "text", "text": "The ZAPHOD beacon hums."}],
        },
    ],
}


async def _setup_project_screenplay(client):
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
    return auth, project_id, screenplay_id


async def _create_character_entity(client, project_id, csrf, name="JOHN"):
    """Create a character entity and return its ID."""
    resp = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "character", "canonical_name": name},
        headers=csrf_headers(csrf),
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_scene_with_character(client, screenplay_id, csrf, character_id):
    """Create a scene using a real character entity ID."""
    content = make_scene_content(character_id)
    resp = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": content},
        headers=csrf_headers(csrf),
    )
    return resp


class TestSceneList:
    async def test_list_scenes_envelope_items(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)

        resp = await client.get(f"/api/screenplays/{screenplay_id}/scenes")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert len(body["items"]) == 1
        assert set(body["items"][0].keys()) == SCENE_FIELDS


class TestSceneCreate:
    async def test_create_scene_201(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        resp = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert set(body.keys()) == SCENE_FIELDS
        assert body["screenplay_id"] == screenplay_id
        assert body["content_hash"]
        assert body["int_ext"] == "INT"
        assert body["time_of_day"] == "DAY"

    async def test_create_scene_requires_csrf(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        content = make_scene_content(char_id)
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": content},
        )
        assert resp.status_code == 403

    async def test_create_scene_after_lock_gets_number(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        await client.post(f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"]))
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        content = make_scene_content(char_id)
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201
        assert resp.json()["number"] == "1"
        assert resp.json()["number_suffix"] is None
        assert resp.json()["locked"] is False

    async def test_create_scene_invalid_content_422(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        invalid_content = {"type": "doc", "content": [{"type": "unknownType"}]}
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": invalid_content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422
        assert isinstance(resp.json()["detail"], str)

    async def test_create_scene_missing_text_block_id_422(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        content = {
            "type": "doc",
            "content": [
                {"type": "action", "text": "Missing id attr"}
            ]
        }
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422

    async def test_create_scene_atom_with_text_422(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "X"}, "text": "not allowed"}
            ]
        }
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 422


class TestSceneGet:
    async def test_get_scene(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}")
        assert resp.status_code == 200
        assert set(resp.json().keys()) == SCENE_FIELDS
        assert resp.json()["id"] == scene_id


class TestSceneUpdate:
    async def test_update_scene_content_recomputes_hash(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]
        old_hash = created.json()["content_hash"]

        new_content = {
            "type": "doc",
            "content": [
                {
                    "type": "sceneHeading",
                    "attrs": {"intExt": "EXT", "location": "PARK", "timeOfDay": "NIGHT"}
                },
                {
                    "type": "action",
                    "attrs": {"id": "n3"},
                    "text": "New action."
                }
            ]
        }
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": new_content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["content_hash"] != old_hash
        assert resp.json()["int_ext"] == "EXT"
        assert resp.json()["time_of_day"] == "NIGHT"

    async def test_update_scene_same_hash_no_change(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]
        old_hash = created.json()["content_hash"]

        content = make_scene_content(char_id)
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert resp.json()["content_hash"] == old_hash

    async def test_update_scene_locked_screenplay_409(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]
        await client.post(f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"]))

        content = make_scene_content(char_id)
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": content},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 409


class TestTiptapContentShape:
    """The frontend Persists editor output as-is: text blocks use Tiptap's
    nested text-node `content`. These mirror src/api/types.ts TiptapNode."""

    async def test_create_accepts_editor_wire_shape(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": TIPTAP_SHAPE_CONTENT},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["content_hash"]
        assert resp.json()["int_ext"] == "INT"

    async def test_patch_accepts_editor_wire_shape(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        created = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={
                "content": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "sceneHeading",
                            "attrs": {
                                "intExt": "INT",
                                "location": "X",
                                "timeOfDay": "DAY",
                            },
                        }
                    ],
                }
            },
            headers=csrf_headers(auth["csrf"]),
        )
        scene_id = created.json()["id"]
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": TIPTAP_SHAPE_CONTENT},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200, resp.text
        got = resp.json()["content"]
        action = next(n for n in got["content"] if n["type"] == "action")
        assert action["content"][0]["text"] == "The ZAPHOD beacon hums."
        assert resp.json()["content_hash"]

    async def test_search_finds_tiptap_nested_text(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": TIPTAP_SHAPE_CONTENT},
            headers=csrf_headers(auth["csrf"]),
        )
        resp = await client.get(
            f"/api/projects/{project_id}/search", params={"q": "ZAPHOD"}
        )
        assert resp.status_code == 200
        assert any("ZAPHOD" in item["match"] for item in resp.json()["items"])

    async def test_existing_text_string_shape_still_accepted(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={
                "content": {
                    "type": "doc",
                    "content": [
                        {"type": "action", "attrs": {"id": "n1"}, "text": "ok"}
                    ],
                }
            },
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201, resp.text


class TestSceneNodeOrder:
    """The backend persists a canonical per-node order so a known node a client
    sends at the tail of a PATCH is pulled back to its prior position instead of
    degrading into a "queue appended to the bottom"."""

    def _ids(self, content):
        return [
            n.get("attrs", {}).get("id")
            for n in content["content"]
            if n.get("type") in ("action", "dialogue", "parenthetical", "shot", "general")
        ]

    async def _create_ordered_scene(self, client, screenplay_id, csrf):
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "A", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a-1"}, "text": "One"},
                {"type": "action", "attrs": {"id": "a-2"}, "text": "Two"},
                {"type": "action", "attrs": {"id": "a-3"}, "text": "Three"},
            ],
        }
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": content},
            headers=csrf_headers(csrf),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def test_known_node_moved_to_tail_is_pulled_back_by_response(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        scene = await self._create_ordered_scene(client, screenplay_id, auth["csrf"])
        scene_id = scene["id"]

        swapped = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "A", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a-2"}, "text": "Two"},
                {"type": "action", "attrs": {"id": "a-3"}, "text": "Three"},
                {"type": "action", "attrs": {"id": "a-1"}, "text": "One"},
            ],
        }
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": swapped},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200, resp.text
        assert self._ids(resp.json()["content"]) == ["a-1", "a-2", "a-3"]

    async def test_known_node_reorder_survives_get(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        scene = await self._create_ordered_scene(client, screenplay_id, auth["csrf"])
        scene_id = scene["id"]

        swapped = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "A", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a-2"}, "text": "Two"},
                {"type": "action", "attrs": {"id": "a-3"}, "text": "Three"},
                {"type": "action", "attrs": {"id": "a-1"}, "text": "One"},
            ],
        }
        await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": swapped},
            headers=csrf_headers(auth["csrf"]),
        )

        get_resp = await client.get(f"/api/scenes/{scene_id}")
        assert get_resp.status_code == 200
        assert self._ids(get_resp.json()["content"]) == ["a-1", "a-2", "a-3"]

    async def test_new_node_inserted_mid_document_keeps_position(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        scene = await self._create_ordered_scene(client, screenplay_id, auth["csrf"])
        scene_id = scene["id"]

        inserted = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "A", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a-1"}, "text": "One"},
                {"type": "action", "attrs": {"id": "x-9"}, "text": "NEW"},
                {"type": "action", "attrs": {"id": "a-2"}, "text": "Two"},
                {"type": "action", "attrs": {"id": "a-3"}, "text": "Three"},
            ],
        }
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": inserted},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200, resp.text
        assert self._ids(resp.json()["content"]) == ["a-1", "x-9", "a-2", "a-3"]

    async def test_deleting_node_removes_it_and_keeps_rest_order(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        scene = await self._create_ordered_scene(client, screenplay_id, auth["csrf"])
        scene_id = scene["id"]

        deleted = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "A", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a-1"}, "text": "One"},
                {"type": "action", "attrs": {"id": "a-3"}, "text": "Three"},
            ],
        }
        resp = await client.patch(
            f"/api/scenes/{scene_id}",
            json={"content": deleted},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200, resp.text
        assert self._ids(resp.json()["content"]) == ["a-1", "a-3"]

    async def test_scene_fields_exclude_node_order(self, client):
        auth, _project_id, screenplay_id = await _setup_project_screenplay(client)
        scene = await self._create_ordered_scene(client, screenplay_id, auth["csrf"])
        assert set(scene.keys()) == SCENE_FIELDS


class TestSceneDelete:
    async def test_delete_scene(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]

        resp = await client.delete(
            f"/api/scenes/{scene_id}", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 204

        list_resp = await client.get(f"/api/screenplays/{screenplay_id}/scenes")
        assert list_resp.json()["items"] == []


class TestSceneReorder:
    async def test_reorder_scene(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        scene1 = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)

        resp = await client.post(
            f"/api/scenes/{scene1.json()['id']}/reorder",
            json={"order_key": 10.0},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 200
        assert "items" in resp.json()
        # Find the reordered scene in the response
        reordered = next(s for s in resp.json()["items"] if s["id"] == scene1.json()["id"])
        assert reordered["order_key"] == 10.0


class TestSceneDuplicate:
    async def test_duplicate_scene(self, client):
        auth, project_id, screenplay_id = await _setup_project_screenplay(client)
        char_id = await _create_character_entity(client, project_id, auth["csrf"])
        created = await _create_scene_with_character(client, screenplay_id, auth["csrf"], char_id)
        scene_id = created.json()["id"]

        resp = await client.post(
            f"/api/scenes/{scene_id}/duplicate",
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201
        assert resp.json()["id"] != scene_id
        assert resp.json()["content_hash"] == created.json()["content_hash"]