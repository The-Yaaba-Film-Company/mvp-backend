import pytest

from app.search import _snippet
from app.validation import _validate_content

pytestmark = pytest.mark.epic(5)

CHAR_ID = "11111111-1111-1111-1111-111111111111"


class TestValidateContent:
    async def test_empty_content_no_issues(self):
        assert _validate_content(None) == []
        assert _validate_content({"type": "doc", "content": []}) == []

    async def test_missing_scene_heading_first(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "action", "attrs": {"id": "a1"}, "text": "Film."},
            ],
        }
        issues = _validate_content(content)
        assert len(issues) == 1
        assert issues[0].type == "warning"
        assert issues[0].node_id == ""
        assert issues[0].message == "Scene without Scene Heading."

    async def test_dialogue_requires_preceding_character(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "ROOM", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a1"}, "text": "Set up."},
                {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Go."},
                {"type": "character", "attrs": {"characterId": CHAR_ID, "displayName": "SAM"}},
                {"type": "dialogue", "attrs": {"id": "d2"}, "text": "Ready."},
            ],
        }
        issues = _validate_content(content)
        flagged = [i for i in issues if i.node_id == "d1"]
        assert len(flagged) == 1
        assert flagged[0].message == "Dialogue has no associated Character."
        assert not any(i.node_id == "d2" for i in issues)

    async def test_parenthetical_requires_preceding_dialogue(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "ROOM", "timeOfDay": "DAY"}},
                {"type": "action", "attrs": {"id": "a1"}, "text": "Set up."},
                {"type": "parenthetical", "attrs": {"id": "p1"}, "text": "(pause)"},
            ],
        }
        issues = _validate_content(content)
        flagged = [i for i in issues if i.node_id == "p1"]
        assert len(flagged) == 1
        assert flagged[0].message == "Parenthetical outside Dialogue."

    async def test_consecutive_characters_flagged_once(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "ROOM", "timeOfDay": "DAY"}},
                {"type": "character", "attrs": {"characterId": CHAR_ID, "displayName": "SAM"}},
                {"type": "character", "attrs": {"characterId": CHAR_ID, "displayName": "SAM"}},
            ],
        }
        issues = _validate_content(content)
        consecutive = [i for i in issues if "consecutive" in i.message.lower()]
        assert len(consecutive) == 1
        assert consecutive[0].node_id == CHAR_ID

    async def test_issue_types_and_ids_unique(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "action", "attrs": {"id": "a1"}, "text": "Film."},
                {"type": "dialogue", "attrs": {"id": "d1"}, "text": "Go."},
            ],
        }
        issues = _validate_content(content)
        assert len(issues) == 2
        assert all(i.type in ("error", "warning", "info") for i in issues)
        assert len({i.id for i in issues}) == 2


class TestSnippet:
    async def test_snippet_mid_text(self):
        text = "A very long line of dialogue text here for the snippet."
        start = text.index("line")
        out = _snippet(text, start, start + len("line"))
        assert "line" in out
        assert out.startswith("...") or "..." in out

    async def test_snippet_at_start(self):
        out = _snippet("hello world", 0, 5)
        assert not out.startswith("...")
        assert out.startswith("hello")

    async def test_snippet_at_end(self):
        out = _snippet("hello world", 6, 11)
        assert out.endswith("world")

    async def test_snippet_full_match_no_ellipsis(self):
        out = _snippet("short", 0, 5)
        assert out == "short"