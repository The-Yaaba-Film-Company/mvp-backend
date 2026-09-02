import pytest

from app.ai import (
    _parse_json_candidates,
    collect_text_nodes,
    normalize_candidate,
    resolve_span,
)

pytestmark = pytest.mark.epic(4)


class TestResolveSpan:
    def test_first_occurrence(self):
        assert resolve_span("John enters with a pistol.", "pistol", []) == (19, 25)

    def test_case_insensitive(self):
        assert resolve_span("A GUN and a Gun.", "gun", []) == (2, 5)

    def test_skips_occupied_spans(self):
        occupied = [(0, 3)]
        span = resolve_span("gun and a gun.", "gun", occupied)
        assert span == (10, 13)

    def test_not_found(self):
        assert resolve_span("hello world", "nope", []) is None

    def test_occupies_whole_text(self):
        assert resolve_span("gun", "gun", [(0, 3)]) is None


class TestCollectTextNodes:
    def test_returns_only_text_blocks(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "sceneHeading", "attrs": {"intExt": "INT", "location": "X"}},
                {"type": "action", "attrs": {"id": "n1"}, "text": "Boom."},
                {"type": "character", "attrs": {"characterId": "c1", "displayName": "JOHN"}},
                {"type": "dialogue", "attrs": {"id": "n2"}, "text": "Hi."},
            ],
        }
        nodes = collect_text_nodes(content)
        assert len(nodes) == 2
        assert nodes[0]["node_id"] == "n1"
        assert nodes[1]["node_id"] == "n2"
        assert nodes[0]["text"] == "Boom."

    def test_handles_missing_attrs_and_nested(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "action", "text": "no id"},
                {
                    "type": "action",
                    "attrs": {"id": "n1"},
                    "content": [{"type": "general", "attrs": {"id": "n1a"}, "text": "nested"}],
                },
            ],
        }
        nodes = collect_text_nodes(content)
        assert [n["node_id"] for n in nodes] == ["n1", "n1a"]
        assert nodes[1]["text"] == "nested"

    def test_empty_content(self):
        assert collect_text_nodes({"type": "doc", "content": []}) == []


class TestNormalizeCandidate:
    def test_valid(self):
        raw = {"node_id": "n1", "matched_text": "pistol", "entity_type": "prop", "suggested_name": "PISTOL", "confidence": 0.92}
        parsed = normalize_candidate(raw)
        assert parsed["entity_type"] == "prop"
        assert parsed["confidence"] == 0.92

    def test_invalid_entity_type_dropped(self):
        raw = {"node_id": "n1", "matched_text": "x", "entity_type": "blob", "suggested_name": "X"}
        assert normalize_candidate(raw) is None

    def test_missing_fields_dropped(self):
        assert normalize_candidate({"node_id": "", "matched_text": "x", "entity_type": "prop", "suggested_name": "X"}) is None
        assert normalize_candidate({"node_id": "n1", "matched_text": "x", "entity_type": "prop", "suggested_name": ""}) is None

    def test_confidence_clamped(self):
        raw = {"node_id": "n1", "matched_text": "x", "entity_type": "prop", "suggested_name": "X", "confidence": 1.7}
        assert normalize_candidate(raw)["confidence"] == 1.0

    def test_confidence_bad_value_null(self):
        raw = {"node_id": "n1", "matched_text": "x", "entity_type": "prop", "suggested_name": "X", "confidence": "oops"}
        assert normalize_candidate(raw)["confidence"] is None


class TestParseJsonCandidates:
    def test_parses_plain_json(self):
        out = _parse_json_candidates('{"candidates": [{"node_id": "n1"}]}')
        assert out == [{"node_id": "n1"}]

    def test_parses_code_fenced_json(self):
        out = _parse_json_candidates('```json\n{"candidates": [{"node_id": "n1"}]}\n```')
        assert out == [{"node_id": "n1"}]

    def test_parses_bare_code_fence(self):
        out = _parse_json_candidates('```\n{"candidates": []}\n```')
        assert out == []

    def test_empty_candidates(self):
        assert _parse_json_candidates('{"candidates": []}') == []

    def test_invalid_json_returns_empty(self):
        assert _parse_json_candidates("not json at all") == []

    def test_empty_content_returns_empty(self):
        assert _parse_json_candidates("") == []

    def test_wrong_shape_returns_empty(self):
        assert _parse_json_candidates('{"foo": 1}') == []
        assert _parse_json_candidates("[1, 2]") == []