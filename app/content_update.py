from typing import Any


def _iter_nodes(content: Any):
    if not isinstance(content, dict):
        return
    stack = [content]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        children = list(node.get("content") or [])
        stack.extend(children)


def update_character_refs(content: dict, old_id: str, new_id: str, new_name: str) -> int:
    """Rewrite every character node binding old_id -> new_id/displayName.

    Returns the number of nodes updated so callers can rebind the JSON column
    only when something actually changed.
    """
    count = 0
    for node in _iter_nodes(content):
        if node.get("type") != "character":
            continue
        attrs = node.get("attrs")
        if not isinstance(attrs, dict):
            continue
        if attrs.get("characterId") != old_id:
            continue
        attrs["characterId"] = new_id
        attrs["displayName"] = new_name
        count += 1
    return count


def update_location_heading(content: dict, old_name: str, new_name: str) -> int:
    """Rewrite the sceneHeading location attr from old_name to new_name."""
    count = 0
    for node in _iter_nodes(content):
        if node.get("type") != "sceneHeading":
            continue
        attrs = node.get("attrs")
        if not isinstance(attrs, dict):
            continue
        if attrs.get("location") != old_name:
            continue
        attrs["location"] = new_name
        count += 1
    return count