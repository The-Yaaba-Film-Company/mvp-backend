import pytest

from app.content_validator import (
    ContentValidationError,
    extract_plain_text,
    node_text,
    validate_tiptap_content,
)


class TestNodeText:
    def test_reads_text_string(self):
        node = {"type": "action", "attrs": {"id": "n1"}, "text": "Hello"}
        assert node_text(node) == "Hello"

    def test_reads_tiptap_nested_text_nodes(self):
        node = {
            "type": "action",
            "attrs": {"id": "n1"},
            "content": [
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "world"},
            ],
        }
        assert node_text(node) == "Hello world"

    def test_returns_empty_string_when_no_text(self):
        assert node_text({"type": "action", "attrs": {"id": "n1"}}) == ""
        assert node_text({"type": "action", "attrs": {"id": "n1"}, "content": []}) == ""
        assert node_text("not a node") == ""
        assert node_text({"type": "action", "attrs": {"id": "n1"}, "content": "nope"}) == ""

    def test_ignores_non_text_children(self):
        node = {
            "type": "action",
            "attrs": {"id": "n1"},
            "content": [
                {"type": "text", "text": "A"},
                {"type": "sceneHeading", "attrs": {"intExt": "INT"}},
            ],
        }
        assert node_text(node) == "A"


class TestValidatorShapes:
    def test_accepts_text_string(self):
        content = {
            "type": "doc",
            "content": [{"type": "action", "attrs": {"id": "n1"}, "text": "ok"}],
        }
        validate_tiptap_content(content)

    def test_accepts_tiptap_nested_text_content(self):
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "action",
                    "attrs": {"id": "n1"},
                    "content": [{"type": "text", "text": "ok"}],
                }
            ],
        }
        validate_tiptap_content(content)

    def test_accepts_empty_content_text_block(self):
        content = {
            "type": "doc",
            "content": [{"type": "action", "attrs": {"id": "n1"}, "content": []}],
        }
        validate_tiptap_content(content)

    def test_rejects_block_without_text(self):
        content = {
            "type": "doc",
            "content": [{"type": "action", "attrs": {"id": "n1"}}],
        }
        with pytest.raises(ContentValidationError):
            validate_tiptap_content(content)

    def test_rejects_non_text_child_in_content(self):
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "action",
                    "attrs": {"id": "n1"},
                    "content": [{"type": "dialogue", "attrs": {"id": "n2"}}],
                }
            ],
        }
        with pytest.raises(ContentValidationError):
            validate_tiptap_content(content)

    def test_still_rejects_missing_id(self):
        content = {
            "type": "doc",
            "content": [
                {
                    "type": "action",
                    "content": [{"type": "text", "text": "no id"}],
                }
            ],
        }
        with pytest.raises(ContentValidationError):
            validate_tiptap_content(content)


class TestExtractPlainText:
    def test_handles_both_shapes(self):
        content = {
            "type": "doc",
            "content": [
                {"type": "action", "attrs": {"id": "a"}, "text": "Line one"},
                {
                    "type": "dialogue",
                    "attrs": {"id": "d"},
                    "content": [{"type": "text", "text": "Line two"}],
                },
            ],
        }
        assert extract_plain_text(content) == "Line oneLine two"