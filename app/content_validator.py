from typing import Any

from fastapi import HTTPException

ALLOWED_ELEMENT_TYPES = (
    "sceneHeading",
    "action",
    "character",
    "dialogue",
    "parenthetical",
    "transition",
    "shot",
    "general",
)

TEXT_BLOCK_TYPES = ("action", "dialogue", "parenthetical", "shot", "general")
ATOM_TYPES = ("sceneHeading", "character", "transition")


class ContentValidationError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=422, detail=detail)


def validate_tiptap_content(content: dict[str, Any]) -> None:
    if not isinstance(content, dict):
        raise ContentValidationError("Content must be a JSON object.")

    if content.get("type") != "doc":
        raise ContentValidationError("Content root must be a 'doc' node.")

    children = content.get("content")
    if not isinstance(children, list):
        raise ContentValidationError("Content 'doc' node must have a 'content' array.")

    _validate_children(children, allow_doc=False)


def _validate_children(nodes: list[dict[str, Any]], *, allow_doc: bool) -> None:
    prev_type: str | None = None
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ContentValidationError(f"Node at index {idx} must be an object.")

        node_type = node.get("type")
        if not isinstance(node_type, str):
            raise ContentValidationError(f"Node at index {idx} must have a string 'type'.")

        if node_type == "doc":
            if not allow_doc:
                raise ContentValidationError("Nested 'doc' nodes are not allowed.")
            _validate_children(node.get("content", []), allow_doc=False)
            continue

        if node_type not in ALLOWED_ELEMENT_TYPES:
            raise ContentValidationError(f"Unknown node type: '{node_type}' at index {idx}.")

        attrs = node.get("attrs")
        if attrs is not None and not isinstance(attrs, dict):
            raise ContentValidationError(f"Node '{node_type}' at index {idx}: 'attrs' must be an object.")

        content_val = node.get("content")
        text_val = node.get("text")


        if node_type in ATOM_TYPES:
            if text_val is not None:
                raise ContentValidationError(f"Atom node '{node_type}' at index {idx} must not have 'text'.")
            _validate_atom_attrs(node_type, attrs, idx)
        elif node_type in TEXT_BLOCK_TYPES:
            if not isinstance(text_val, str) and not _is_text_content(content_val):
                raise ContentValidationError(
                    f"Text block '{node_type}' at index {idx} must carry text as a "
                    "'text' string or a 'content' array of text nodes."
                )
            if not attrs or not isinstance(attrs.get("id"), str):
                raise ContentValidationError(f"Text block '{node_type}' at index {idx} must have attrs.id (string).")
        else:
            raise ContentValidationError(f"Unhandled node type: '{node_type}' at index {idx}.")

        # _validate_order(prev_type, node_type, idx)
        prev_type = node_type


def _is_text_content(content_val: Any) -> bool:
    """True when `content` is the Tiptap wire shape for a text block's marks:
    a list of `{"type": "text", "text": ...}` nodes (possibly empty)."""
    if not isinstance(content_val, list):
        return False
    return all(
        isinstance(child, dict)
        and child.get("type") == "text"
        and isinstance(child.get("text"), str)
        for child in content_val
    )


def node_text(node: dict[str, Any]) -> str:
    """A text block's string content from either canonical form the editor may
    send: a `text` string on the node, or Tiptap's nested
    `content: [{"type": "text", "text": ...}]`. Returns '' when absent."""
    if not isinstance(node, dict):
        return ""
    text_val = node.get("text")
    if isinstance(text_val, str):
        return text_val
    parts: list[str] = []
    for child in node.get("content") or []:
        if (
            isinstance(child, dict)
            and child.get("type") == "text"
            and isinstance(child.get("text"), str)
        ):
            parts.append(child["text"])
    return "".join(parts)


def _validate_atom_attrs(node_type: str, attrs: dict[str, Any] | None, idx: int) -> None:
    if node_type == "sceneHeading":
        if not attrs:
            raise ContentValidationError(f"sceneHeading at index {idx} must have attrs.")
        int_ext = attrs.get("intExt")
        if int_ext not in ("INT", "EXT", "INT_EXT"):
            raise ContentValidationError(f"sceneHeading at index {idx}: attrs.intExt must be INT, EXT, or INT_EXT.")
        if not isinstance(attrs.get("location"), str):
            raise ContentValidationError(f"sceneHeading at index {idx}: attrs.location must be a string.")
        if attrs.get("timeOfDay") is not None and not isinstance(attrs.get("timeOfDay"), str):
            raise ContentValidationError(f"sceneHeading at index {idx}: attrs.timeOfDay must be a string if present.")
        if attrs.get("modifier") is not None and not isinstance(attrs.get("modifier"), str):
            raise ContentValidationError(f"sceneHeading at index {idx}: attrs.modifier must be a string if present.")
    elif node_type == "character":
        if not attrs:
            raise ContentValidationError(f"character at index {idx} must have attrs.")
        if not isinstance(attrs.get("characterId"), str) and attrs.get("characterId") is not None:
            raise ContentValidationError(f"character at index {idx}: attrs.characterId must be a string.")
        if not isinstance(attrs.get("displayName"), str):
            raise ContentValidationError(f"character at index {idx}: attrs.displayName must be a string.")
        if attrs.get("extension") is not None and not isinstance(attrs.get("extension"), str):
            raise ContentValidationError(f"character at index {idx}: attrs.extension must be a string if present.")
    elif node_type == "transition":
        if attrs and attrs.get("transitionType") is not None and not isinstance(attrs.get("transitionType"), str):
            raise ContentValidationError(f"transition at index {idx}: attrs.transitionType must be a string if present.")


# def _validate_order(prev_type: str | None, curr_type: str, idx: int) -> None:
#     order = {t: i for i, t in enumerate(ALLOWED_ELEMENT_TYPES)}
#     prev_order = order.get(prev_type, -1)
#     curr_order = order.get(curr_type, -1)
#     if prev_order > curr_order:
#         raise ContentValidationError(f"Invalid element order: '{curr_type}' at index {idx} cannot follow '{prev_type} {curr_order}'.")


def extract_plain_text(content: dict[str, Any]) -> str:
    texts: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") in TEXT_BLOCK_TYPES:
            texts.append(node_text(node))
        for child in node.get("content", []) or []:
            walk(child)

    walk(content)
    return "".join(texts)


def compute_content_hash(content: dict[str, Any]) -> str:
    import hashlib
    plain = extract_plain_text(content)
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def extract_heading_info(content: dict[str, Any]) -> dict[str, Any]:
    result = {
        "int_ext": None,
        "location": None,
        "time_of_day": None,
        "heading_modifier": None,
    }

    def walk(node: dict[str, Any]) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "sceneHeading":
            attrs = node.get("attrs", {})
            result["int_ext"] = attrs.get("intExt")
            result["location"] = attrs.get("location")
            result["time_of_day"] = attrs.get("timeOfDay")
            result["heading_modifier"] = attrs.get("modifier")
            return
        for child in node.get("content", []) or []:
            walk(child)

    walk(content)
    return result