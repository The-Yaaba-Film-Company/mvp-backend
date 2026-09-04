from app.content_order import (
    extract_order,
    reconcile_order,
    sort_content_by_order,
)


def _block(type_: str, attrs=None):
    node = {"type": type_}
    if attrs is not None:
        node["attrs"] = attrs
    return node


def _doc(blocks):
    return {"type": "doc", "content": blocks}


class TestExtractOrder:
    def test_text_block_id_is_the_key(self):
        content = _doc(
            [
                _block("action", {"id": "a-1", "intExt": "INT"}),
                _block("dialogue", {"id": "d-2"}),
            ]
        )
        assert extract_order(content) == ["a-1", "d-2"]

    def test_atoms_get_synthetic_positional_keys(self):
        content = _doc(
            [
                _block("sceneHeading", {"intExt": "INT", "location": "HOUSE"}),
                _block("action", {"id": "a-1"}),
                _block("sceneHeading", {"intExt": "EXT", "location": "STREET"}),
                _block("transition", {"transitionType": "CUT TO:"}),
            ]
        )
        assert extract_order(content) == [
            "sceneHeading@0",
            "a-1",
            "sceneHeading@1",
            "transition@0",
        ]

    def test_nested_text_runs_are_not_blocks(self):
        content = _doc(
            [
                {
                    "type": "action",
                    "attrs": {"id": "a-1"},
                    "content": [{"type": "text", "text": "Hello"}],
                }
            ]
        )
        assert extract_order(content) == ["a-1"]


class TestReconcileOrder:
    def test_known_node_sent_at_tail_is_pulled_back(self):
        prev = ["a-1", "d-2", "a-3"]
        incoming = ["d-2", "a-3", "a-1"]
        assert reconcile_order(prev, incoming) == ["a-1", "d-2", "a-3"]

    def test_new_node_inserted_mid_document_is_kept_in_place(self):
        prev = ["a-1", "d-2", "a-3"]
        incoming = ["a-1", "x-9", "d-2", "a-3"]
        assert reconcile_order(prev, incoming) == ["a-1", "x-9", "d-2", "a-3"]

    def test_new_node_appended_to_end_stays_at_end(self):
        prev = ["a-1", "d-2"]
        incoming = ["a-1", "d-2", "x-9"]
        assert reconcile_order(prev, incoming) == ["a-1", "d-2", "x-9"]

    def test_deleted_keys_are_dropped(self):
        prev = ["a-1", "d-2", "a-3"]
        incoming = ["a-1", "a-3"]
        assert reconcile_order(prev, incoming) == ["a-1", "a-3"]

    def test_multiple_new_keys_preserve_incoming_relative_order(self):
        prev = ["a-1", "d-2"]
        incoming = ["a-1", "x-9", "y-8", "d-2"]
        assert reconcile_order(prev, incoming) == ["a-1", "x-9", "y-8", "d-2"]

    def test_no_previous_order_returns_incoming(self):
        incoming = ["a-1", "d-2", "a-3"]
        assert reconcile_order([], incoming) == incoming

    def test_no_change_returns_same_order(self):
        prev = ["a-1", "d-2", "a-3"]
        assert reconcile_order(prev, list(prev)) == prev


class TestSortContentByOrder:
    def test_reorders_blocks_to_match_order(self):
        content = _doc(
            [
                _block("action", {"id": "a-1", "intExt": "INT"}),
                _block("dialogue", {"id": "d-2"}),
                _block("action", {"id": "a-3", "intExt": "EXT"}),
            ]
        )
        sorted_content = sort_content_by_order(content, ["a-3", "a-1", "d-2"])
        assert [b.get("attrs", {}).get("id") for b in sorted_content["content"]] == [
            "a-3",
            "a-1",
            "d-2",
        ]

    def test_orders_atom_synthetic_keys_positionally(self):
        content = _doc(
            [
                _block("sceneHeading", {"intExt": "INT", "location": "HOUSE"}),
                _block("action", {"id": "a-1"}),
                _block("transition", {"transitionType": "CUT TO:"}),
            ]
        )
        order = ["transition@0", "a-1", "sceneHeading@0"]
        sorted_content = sort_content_by_order(content, order)
        assert [b["type"] for b in sorted_content["content"]] == [
            "transition",
            "action",
            "sceneHeading",
        ]

    def test_unknown_keys_appended_after_known(self):
        content = _doc(
            [
                _block("action", {"id": "a-1"}),
                _block("action", {"id": "x-9"}),
                _block("dialogue", {"id": "d-2"}),
            ]
        )
        sorted_content = sort_content_by_order(content, ["a-1", "d-2"])
        assert [b.get("attrs", {}).get("id") for b in sorted_content["content"]] == [
            "a-1",
            "d-2",
            "x-9",
        ]

    def test_returns_shallow_copy_and_preserves_root(self):
        content = _doc([_block("action", {"id": "a-1"})])
        sorted_content = sort_content_by_order(content, ["a-1"])
        assert sorted_content["type"] == "doc"
        assert sorted_content is not content
        assert sorted_content["content"] is not content["content"]
