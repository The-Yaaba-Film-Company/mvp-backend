from typing import Any

TEXT_BLOCK_TYPES = ("action", "dialogue", "parenthetical", "shot", "general")


def _block_key(node: dict[str, Any], occ: dict[str, int]) -> str:
    """Unique stable key for a top-level block.

    Text blocks carry a stable client-generated `attrs.id`; atoms
    (sceneHeading/character/transition) carry no id, so they get a synthetic
    positional key scoped per type.
    """
    node_type = node.get("type")
    if node_type in TEXT_BLOCK_TYPES:
        attrs = node.get("attrs") or {}
        return attrs.get("id")
    cnt = occ.get(node_type, 0)
    occ[node_type] = cnt + 1
    return f"{node_type}@{cnt}"


def extract_order(content: dict[str, Any]) -> list[str]:
    """The order of top-level blocks in document order as a list of keys."""
    occ: dict[str, int] = {}
    keys: list[str] = []
    for node in content.get("content") or []:
        if isinstance(node, dict):
            keys.append(_block_key(node, occ))
    return keys


def reconcile_order(prev: list[str], incoming: list[str]) -> list[str]:
    """Return a canonical block order given the previously stored order and the
    order the client just sent.

    - Keys present in both keep their *previous* relative order, so a known
      node the client sends at the tail is pulled back to its prior position
      (repairs "appended to the bottom").
    - Brand-new keys (including freshly inserted atoms/text blocks) are placed
      where they first appear relative to their neighbors in the incoming array.
    - Deleted keys are dropped.
    """
    prev_present = set(incoming)
    known: list[str] = []
    for k in prev:
        if k in prev_present and k not in known:
            known.append(k)

    new: list[str] = []
    for k in incoming:
        if k not in known and k not in prev and k not in new:
            # `prev` is authoritative for known keys; anything else is new
            new.append(k)

    if not new:
        return known

    known_index = {k: i for i, k in enumerate(known)}
    incoming_pos = {k: i for i, k in enumerate(incoming)}

    buckets: dict[int, list[str]] = {i: [] for i in range(len(known) + 1)}
    for k in new:
        bucket = len(known)
        k_pos = incoming_pos[k]
        for kk in known:
            if incoming_pos[kk] > k_pos:
                idx = known_index[kk]
                bucket = min(bucket, idx)
        buckets[bucket].append(k)

    result: list[str] = []
    for i in range(len(known)):
        result.extend(buckets[i])
        result.append(known[i])
    result.extend(buckets[len(known)])
    return result


def sort_content_by_order(content: dict[str, Any], order: list[str]) -> dict[str, Any]:
    """Return a shallow copy of `content` whose top-level blocks are reordered
    to match `order`. Blocks whose key is not in `order` keep their relative
    order and are appended after the known ones.
    """
    children = list(content.get("content") or [])
    occ: dict[str, int] = {}
    keyed: list[tuple[str, dict[str, Any]]] = []
    for node in children:
        if isinstance(node, dict):
            keyed.append((_block_key(node, occ), node))

    by_key: dict[str, list[dict[str, Any]]] = {}
    for key, node in keyed:
        by_key.setdefault(key, []).append(node)

    used: set[str] = set()
    out: list[dict[str, Any]] = []
    for key in order:
        nodes = by_key.get(key)
        if nodes:
            out.append(nodes.pop(0))
            used.add(key)

    out.extend(node for key, node in keyed if key not in used)

    return {**content, "content": out}
