import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(6)


def make_scene_content(character_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "ROOM", "timeOfDay": "DAY"}},
            {"type": "action", "attrs": {"id": "n1"}, "text": "John enters."},
            {"type": "character", "attrs": {"characterId": character_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "n2"}, "text": "Hello."},
        ],
    }


async def _setup(client, n: int = 3):
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
    char = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "character", "canonical_name": "JOHN"},
        headers=csrf_headers(auth["csrf"]),
    )
    char_id = char.json()["id"]
    scene_ids = []
    for _ in range(n):
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": make_scene_content(char_id)},
            headers=csrf_headers(auth["csrf"]),
        )
        scene_ids.append(resp.json()["id"])
    return auth, project_id, screenplay_id, char_id, scene_ids


async def _scenes(client, screenplay_id):
    resp = await client.get(f"/api/screenplays/{screenplay_id}/scenes")
    assert resp.status_code == 200
    return resp.json()["items"]


class TestLock:
    async def test_lock_assigns_sequential_numbers(self, client):
        auth, _p, screenplay_id, _c, scene_ids = await _setup(client)
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200
        assert resp.json()["locked_at"] is not None

        scenes = await _scenes(client, screenplay_id)
        assert [s["number"] for s in scenes] == ["1", "2", "3"]
        assert all(s["number_suffix"] is None for s in scenes)
        assert all(s["locked"] for s in scenes)
        assert [s["id"] for s in scenes] == scene_ids


class TestPostLockInsert:
    async def test_create_after_lock_gets_next_number(self, client):
        auth, _p, screenplay_id, _c, _scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        char_id = _c
        resp = await client.post(
            f"/api/screenplays/{screenplay_id}/scenes",
            json={"content": make_scene_content(char_id)},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 201, resp.text
        scene = resp.json()
        assert scene["number"] == "4"
        assert scene["number_suffix"] is None
        assert scene["locked"] is False

        scenes = await _scenes(client, screenplay_id)
        assert [s["number"] for s in scenes] == ["1", "2", "3", "4"]
        assert [s["number_suffix"] for s in scenes] == [None, None, None, None]

    async def test_create_after_lock_appends_more(self, client):
        auth, _p, screenplay_id, char_id, _scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        for _ in range(2):
            resp = await client.post(
                f"/api/screenplays/{screenplay_id}/scenes",
                json={"content": make_scene_content(char_id)},
                headers=csrf_headers(auth["csrf"]),
            )
            assert resp.status_code == 201

        scenes = await _scenes(client, screenplay_id)
        assert [s["number"] for s in scenes] == ["1", "2", "3", "4", "5"]
        assert [s["number_suffix"] for s in scenes] == [None] * 5

    async def test_duplicate_after_lock_gets_next_number(self, client):
        auth, _p, screenplay_id, _c, scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        resp = await client.post(
            f"/api/scenes/{scene_ids[-1]}/duplicate", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 201, resp.text
        dup = resp.json()
        assert dup["number"] == "4"
        assert dup["number_suffix"] is None

        scenes = await _scenes(client, screenplay_id)
        assert [s["number"] for s in scenes] == ["1", "2", "3", "4"]

    async def test_update_after_lock_still_409(self, client):
        auth, _p, screenplay_id, _c, scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        resp = await client.patch(
            f"/api/scenes/{scene_ids[0]}",
            json={"int_ext": "EXT"},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 409

    async def test_delete_after_lock_still_409(self, client):
        auth, _p, screenplay_id, _c, scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        resp = await client.delete(
            f"/api/scenes/{scene_ids[0]}", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 409

    async def test_reorder_after_lock_409(self, client):
        auth, _p, screenplay_id, _c, scene_ids = await _setup(client)
        await client.post(
            f"/api/screenplays/{screenplay_id}/lock", headers=csrf_headers(auth["csrf"])
        )
        resp = await client.post(
            f"/api/scenes/{scene_ids[0]}/reorder",
            json={"order_key": 10.0},
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 409