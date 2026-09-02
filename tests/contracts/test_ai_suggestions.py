import pytest

from tests.conftest import csrf_headers, register

pytestmark = pytest.mark.epic(4)

SUGGESTION_FIELDS = {
    "id", "scene_id", "node_id", "matched_text", "start_offset", "end_offset",
    "suggested_type", "suggested_name", "matched_entity_id", "confidence",
    "model", "prompt_version", "status", "reviewed_by", "reviewed_at", "created_at",
}


def make_scene_content(character_id: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "POLICE STATION", "timeOfDay": "DAY"}},
            {"type": "action", "attrs": {"id": "n1"}, "text": "John enters with a pistol."},
            {"type": "character", "attrs": {"characterId": character_id, "displayName": "JOHN"}},
            {"type": "dialogue", "attrs": {"id": "n2"}, "text": "Hello there."},
        ],
    }


CANDIDATE = {
    "node_id": "n1",
    "matched_text": "pistol",
    "entity_type": "prop",
    "suggested_name": "PISTOL",
    "confidence": 0.92,
}


async def _setup_scene(client, app, with_prop=False):
    """Create project/screenplay/scene. API key set only AFTER scene creation so the
    background auto-analyze is not triggered during setup (tests call ai-suggest manually)."""
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

    prop_id = None
    if with_prop:
        prop_resp = await client.post(
            f"/api/projects/{project_id}/entities",
            json={"entity_type": "prop", "canonical_name": "PISTOL"},
            headers=csrf_headers(auth["csrf"]),
        )
        prop_id = prop_resp.json()["id"]

    scene_resp = await client.post(
        f"/api/screenplays/{screenplay_id}/scenes",
        json={"content": make_scene_content(char_id)},
        headers=csrf_headers(auth["csrf"]),
    )
    scene_id = scene_resp.json()["id"]

    app.state.settings.nvidia_api_key = "test-key"
    return auth, project_id, screenplay_id, scene_id, char_id, prop_id


def _stub_call_model(monkeypatch):
    class Stub:
        calls = 0

        async def __call__(self, client, nodes):
            Stub.calls += 1
            return [dict(CANDIDATE)]

    monkeypatch.setattr("app.ai._call_model", Stub())
    return Stub


class TestAiSuggest:
    async def test_ai_suggest_returns_items(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)

        resp = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert set(item.keys()) == SUGGESTION_FIELDS
        assert item["node_id"] == "n1"
        assert item["matched_text"] == "pistol"
        assert item["suggested_type"] == "prop"
        assert item["suggested_name"] == "PISTOL"
        assert item["status"] == "pending"
        assert item["start_offset"] == 19
        assert item["end_offset"] == 25
        assert item["confidence"] == 0.92


    async def test_ai_suggest_matches_existing_entity(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, prop_id = await _setup_scene(client, app, with_prop=True)

        resp = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200
        assert resp.json()["items"][0]["matched_entity_id"] == prop_id

    async def test_ai_suggest_short_circuits_on_unchanged_hash(self, client, app, monkeypatch):
        stub = _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)

        first = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        assert second.status_code == 200
        assert len(second.json()["items"]) == 1
        assert stub.calls == 1

    async def test_ai_suggest_no_api_key_503(self, client, app):
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        app.state.settings.nvidia_api_key = ""

        resp = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 503

    async def test_ai_suggest_requires_csrf(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        _auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)

        resp = await client.post(f"/api/scenes/{scene_id}/ai-suggest")
        assert resp.status_code == 403


class TestSuggestionList:
    async def test_list_suggestions_envelope_items(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )

        resp = await client.get(f"/api/scenes/{scene_id}/ai-suggestions")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert len(body["items"]) == 1
        assert body["items"][0]["scene_id"] == scene_id


class TestSuggestionAccept:
    async def test_accept_creates_entity_and_annotation(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        suggest = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        suggestion_id = suggest.json()["items"][0]["id"]

        resp = await client.post(
            f"/api/ai-suggestions/{suggestion_id}/accept", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "accepted"
        assert resp.json()["reviewed_by"] == auth["user"]["id"]

        # Entity created
        entities = await client.get(f"/api/projects/{project_id}/entities?type=prop")
        assert entities.status_code == 200
        names = [e["canonical_name"] for e in entities.json()]
        assert "PISTOL" in names
        entity_id = next(e["id"] for e in entities.json() if e["canonical_name"] == "PISTOL")

        # Annotation created with ai_accepted source
        anns = await client.get(f"/api/scenes/{scene_id}/annotations")
        assert anns.status_code == 200
        assert len(anns.json()["items"]) == 1
        ann = anns.json()["items"][0]
        assert ann["source"] == "ai_accepted"
        assert ann["entity_id"] == entity_id
        assert ann["node_id"] == "n1"

    async def test_accept_reuses_matched_entity(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, project_id, _screenplay_id, scene_id, _char_id, prop_id = await _setup_scene(client, app, with_prop=True)
        suggest = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        suggestion_id = suggest.json()["items"][0]["id"]

        resp = await client.post(
            f"/api/ai-suggestions/{suggestion_id}/accept", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200
        assert resp.json()["matched_entity_id"] == prop_id

        entities = await client.get(f"/api/projects/{project_id}/entities?type=prop")
        assert len(entities.json()) == 1
        assert entities.json()[0]["id"] == prop_id
        anns = await client.get(f"/api/scenes/{scene_id}/annotations")
        assert len(anns.json()["items"]) == 1
        assert anns.json()["items"][0]["entity_id"] == prop_id

    async def test_accept_already_reviewed_409(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        suggest = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        suggestion_id = suggest.json()["items"][0]["id"]
        await client.post(
            f"/api/ai-suggestions/{suggestion_id}/accept", headers=csrf_headers(auth["csrf"])
        )

        resp = await client.post(
            f"/api/ai-suggestions/{suggestion_id}/accept", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 409

    async def test_accept_unknown_404(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, _scene_id, _char_id, _ = await _setup_scene(client, app)

        resp = await client.post(
            "/api/ai-suggestions/00000000-0000-0000-0000-000000000000/accept",
            headers=csrf_headers(auth["csrf"]),
        )
        assert resp.status_code == 404

    async def test_accept_requires_csrf(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        _auth, _project_id, _screenplay_id, _scene_id, _char_id, _ = await _setup_scene(client, app)

        resp = await client.post("/api/ai-suggestions/00000000-0000-0000-0000-000000000000/accept")
        assert resp.status_code == 403


class TestSuggestionReject:
    async def test_reject_flips_status(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        suggest = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        suggestion_id = suggest.json()["items"][0]["id"]

        resp = await client.post(
            f"/api/ai-suggestions/{suggestion_id}/reject", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        assert resp.json()["reviewed_at"] is not None

        # No annotation created
        anns = await client.get(f"/api/scenes/{scene_id}/annotations")
        assert anns.json()["items"] == []

    async def test_reject_already_reviewed_409(self, client, app, monkeypatch):
        _stub_call_model(monkeypatch)
        auth, _project_id, _screenplay_id, scene_id, _char_id, _ = await _setup_scene(client, app)
        suggest = await client.post(
            f"/api/scenes/{scene_id}/ai-suggest", headers=csrf_headers(auth["csrf"])
        )
        suggestion_id = suggest.json()["items"][0]["id"]
        await client.post(
            f"/api/ai-suggestions/{suggestion_id}/reject", headers=csrf_headers(auth["csrf"])
        )

        resp = await client.post(
            f"/api/ai-suggestions/{suggestion_id}/reject", headers=csrf_headers(auth["csrf"])
        )
        assert resp.status_code == 409


class TestAutoAnalyze:
    async def test_auto_analyze_on_scene_create(self, client, app, monkeypatch):
        stub = _stub_call_model(monkeypatch)
        app.state.settings.nvidia_api_key = "test-key"
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
            json={"content": make_scene_content(char_id)},
            headers=csrf_headers(auth["csrf"]),
        )
        scene_id = scene_resp.json()["id"]

        suggestions = await client.get(f"/api/scenes/{scene_id}/ai-suggestions")
        assert suggestions.status_code == 200
        assert len(suggestions.json()["items"]) == 1
        assert stub.calls == 1