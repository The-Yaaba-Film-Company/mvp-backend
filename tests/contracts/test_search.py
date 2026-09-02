import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(5)

SEARCH_FIELDS = {
    "id", "kind", "match", "snippet", "scene_id",
    "node_id", "start_offset", "end_offset",
}
KINDS = {"character", "location", "scene", "entity", "text"}


def scene1_content(char_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "POLICE STATION", "timeOfDay": "NIGHT"}},
            {"type": "action", "attrs": {"id": "a1"}, "text": "Detective John draws his service weapon."},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Drop it."},
        ],
    }


def scene2_content(char_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "EXT", "location": "PARK", "timeOfDay": "DAY"}},
            {"type": "action", "attrs": {"id": "a2"}, "text": "A brass pistol glints near the fountain."},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "d2"}, "text": "Easy now."},
        ],
    }


async def _setup_project(client, with_prop_annotation=True):
    """Project with two scenes; optional prop annotation on scene 2.

    Returns (auth, project_id, screenplay_id, scene1_id, scene2_id, char_id, prop_id).
    """
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
    prop = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "prop", "canonical_name": "PISTOL"},
        headers=csrf_headers(auth["csrf"]),
    )
    prop_id = prop.json()["id"]

    s1 = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": scene1_content(char_id)},
        headers=csrf_headers(auth["csrf"]),
    )
    scene1_id = s1.json()["id"]
    s2 = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": scene2_content(char_id)},
        headers=csrf_headers(auth["csrf"]),
    )
    scene2_id = s2.json()["id"]

    if with_prop_annotation:
        text = "A brass pistol glints near the fountain."
        start = text.index("pistol")
        ann = await client.post(
            f"/api/scenes/{scene2_id}/annotations",
            json={
                "node_id": "a2",
                "start_offset": start,
                "end_offset": start + len("pistol"),
                "entity_id": prop_id,
            },
            headers=csrf_headers(auth["csrf"]),
        )
        assert ann.status_code == 201, ann.text

    return auth, project_id, screenplay_id, scene1_id, scene2_id, char_id, prop_id


def _text_results(items):
    return [i for i in items if i["kind"] == "text"]


class TestSearch:
    async def test_search_envelope_and_fields(self, client):
        _auth, project_id, _sp, _s1, s2_id, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert body["items"]
        for item in body["items"]:
            assert set(item.keys()) == SEARCH_FIELDS
            assert item["kind"] in KINDS
            assert isinstance(item["match"], str) and item["match"]
            assert isinstance(item["snippet"], str)
            assert item["scene_id"] is None or isinstance(item["scene_id"], str)
            assert item["node_id"] is None or isinstance(item["node_id"], str)
            assert item["start_offset"] is None or isinstance(item["start_offset"], int)
            assert item["end_offset"] is None or isinstance(item["end_offset"], int)
        assert any(i["kind"] == "entity" for i in body["items"])
        assert any(i["kind"] == "text" and i["scene_id"] == s2_id for i in body["items"])

    async def test_search_entity_match(self, client):
        _auth, project_id, _sp, _s1, s2_id, _c, prop_id = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        entity_results = [i for i in resp.json()["items"] if i["kind"] == "entity"]
        assert entity_results
        hit = entity_results[0]
        assert hit["match"] == "PISTOL"
        assert hit["id"] == prop_id
        assert hit["scene_id"] == s2_id

    async def test_search_character_kind(self, client):
        _auth, project_id, _sp, s1_id, _s2, char_id, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "john"})
        char_results = [i for i in resp.json()["items"] if i["kind"] == "character"]
        assert char_results
        hit = char_results[0]
        assert hit["match"] == "JOHN"
        assert hit["id"] == char_id
        assert hit["scene_id"] == s1_id

    async def test_search_scene_match(self, client):
        _auth, project_id, _sp, s1_id, _s2, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "police"})
        scene_results = [i for i in resp.json()["items"] if i["kind"] == "scene"]
        assert scene_results
        hit = scene_results[0]
        assert "POLICE STATION" in hit["match"]
        assert hit["scene_id"] == s1_id
        assert hit["node_id"] is None
        assert hit["start_offset"] is None

    async def test_search_text_match_with_offsets(self, client):
        _auth, project_id, _sp, _s1, s2_id, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        text_results = _text_results(resp.json()["items"])
        assert text_results
        hit = text_results[0]
        assert hit["match"] == "pistol"
        assert hit["node_id"] == "a2"
        assert hit["scene_id"] == s2_id
        assert hit["start_offset"] == 8
        assert hit["end_offset"] == 14
        assert "pistol" in hit["snippet"]

    async def test_search_types_filter(self, client):
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)

        prop_resp = await client.get(
            f"/api/projects/{project_id}/search", params={"q": "pistol", "types": "prop"}
        )
        items = prop_resp.json()["items"]
        assert items
        for item in items:
            assert item["kind"] == "entity"

        char_resp = await client.get(
            f"/api/projects/{project_id}/search", params={"q": "pistol", "types": "character"}
        )
        assert char_resp.json()["items"] == []

    async def test_search_no_q_returns_empty(self, client):
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_search_no_match_returns_empty(self, client):
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "zzzzzz"})
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_search_requires_session(self, client):
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)
        client.cookies.clear()
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        assert resp.status_code == 401

    async def test_search_non_member_forbidden(self, client, make_client):
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)
        other = make_client()
        await register(other, email="bob@example.com", display_name="Bob")
        resp = await other.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        assert resp.status_code == 403

    async def test_search_get_requires_no_csrf(self, client):
        # GETs skip CSRF; the project-scoped role check still needs a session
        _auth, project_id, _sp, _s1, _s2, _c, _prop = await _setup_project(client)
        resp = await client.get(f"/api/projects/{project_id}/search", params={"q": "pistol"})
        assert resp.status_code == 200