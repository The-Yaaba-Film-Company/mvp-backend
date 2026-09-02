import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(5)

VALIDATION_FIELDS = {"id", "type", "message", "node_id"}

VALID_TYPES = {"error", "warning", "info"}


def content_multirule(char_id: str) -> dict:
    # action then dialogue: dialogue-without-character (prev is action). Order stays valid.
    return {
        "type": "doc",
        "content": [
            {"type": "action", "attrs": {"id": "a1"}, "text": "John enters."},
            {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Hello."},
            {"type": "parenthetical", "attrs": {"id": "p1"}, "text": "(beat)"},
        ],
    }


def content_consecutive(char_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "action", "attrs": {"id": "a1"}, "text": "John enters."},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": "JOHN"}},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Hello."},
        ],
    }


def content_parenthetical(char_id: str) -> dict:
    # parenthetical right after an action block (outside dialogue)
    return {
        "type": "doc",
        "content": [
            {"type": "action", "attrs": {"id": "a1"}, "text": "John enters."},
            {"type": "parenthetical", "attrs": {"id": "p1"}, "text": "(beat)"},
        ],
    }


def clean_content(char_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "POLICE STATION", "timeOfDay": "DAY"}},
            {"type": "action", "attrs": {"id": "a1"}, "text": "John enters."},
            {"type": "character", "attrs": {"characterId": char_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Hello."},
        ],
    }


async def _setup_scene(client, content: dict):
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
    char_resp = await client.post(
        f"/api/projects/{project_id}/entities",
        json={"entity_type": "character", "canonical_name": "JOHN"},
        headers=csrf_headers(auth["csrf"]),
    )
    char_id = char_resp.json()["id"]
    scene_resp = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": content(char_id)},
        headers=csrf_headers(auth["csrf"]),
    )
    return auth, project_id, scene_resp


class TestValidation:
    async def test_validation_envelope_and_fields(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, content_multirule)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) >= 2
        for item in body["items"]:
            assert set(item.keys()) == VALIDATION_FIELDS
            assert item["type"] in VALID_TYPES
            assert isinstance(item["message"], str) and item["message"]
            assert isinstance(item["node_id"], str)

    async def test_validation_missing_scene_heading(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, content_multirule)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        issues = resp.json()["items"]
        heading_issues = [i for i in issues if "Scene Heading" in i["message"]]
        assert len(heading_issues) == 1
        assert heading_issues[0]["type"] == "warning"
        assert heading_issues[0]["node_id"] == ""

    async def test_validation_dialogue_without_character(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, content_multirule)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        issues = resp.json()["items"]
        # d1 follows an action block, not a character → flagged
        flagged = [i for i in issues if i["node_id"] == "d1"]
        assert len(flagged) == 1
        assert flagged[0]["type"] == "warning"
        assert "Character" in flagged[0]["message"]

    async def test_validation_parenthetical_outside_dialogue(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, content_parenthetical)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        issues = resp.json()["items"]
        # p1 follows an action block (outside a dialogue), not a dialogue → flagged
        flagged = [i for i in issues if i["node_id"] == "p1"]
        assert len(flagged) == 1
        assert flagged[0]["type"] == "warning"
        assert "Parenthetical" in flagged[0]["message"]

    async def test_validation_consecutive_characters(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, content_consecutive)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        issues = resp.json()["items"]
        consecutive = [i for i in issues if "consecutive" in i["message"].lower() or "Character nodes" in i["message"]]
        assert len(consecutive) == 1
        assert consecutive[0]["type"] == "warning"
        # the flagged node references the second character (characterId)
        assert consecutive[0]["node_id"] != ""

    async def test_validation_clean_scene_has_no_issues(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, clean_content)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_validation_get_mutating_csrf_not_required(self, client, app):
        # GET endpoints skip CSRF entirely
        _auth, _project_id, scene_resp = await _setup_scene(client, clean_content)
        scene_id = scene_resp.json()["id"]

        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        assert resp.status_code == 200

    async def test_validation_requires_session(self, client, app):
        _auth, _project_id, scene_resp = await _setup_scene(client, clean_content)
        scene_id = scene_resp.json()["id"]

        client.cookies.clear()
        resp = await client.get(f"/api/scenes/{scene_id}/validation")
        assert resp.status_code == 401

    async def test_validation_unknown_scene_404(self, client):
        await register(client)
        resp = await client.get(
            "/api/scenes/00000000-0000-0000-0000-000000000000/validation"
        )
        assert resp.status_code == 404

    async def test_validation_non_member_forbidden(self, client, app, make_client):
        _auth, _project_id, scene_resp = await _setup_scene(client, clean_content)
        scene_id = scene_resp.json()["id"]

        other = make_client()
        await register(other, email="bob@example.com", display_name="Bob")
        resp = await other.get(f"/api/scenes/{scene_id}/validation")
        assert resp.status_code == 403