import json
from uuid import UUID

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.content_validator import node_text
from app.models import EntityType
from app.schema import ENTITY_TYPES
from app.sentry import record_ai_call, record_ai_result

NVIDIA_MODEL = "meta/muse-glimmer-30b"
PROMPT_VERSION = "1"

_AI_PROMPT = """You are an entity extractor for a screenplay editor. Analyze the scene
text blocks below. Each line is `[node_id] text`.

Extract production entities (props, locations, vehicles, equipment, etc.) mentioned in
the text. Do NOT flag characters that already have their own Character node and do NOT
flag the scene heading location (it lives in its own heading).

Rules:
- matched_text must be the exact substring of that node's text (verbatim, same case).
- suggested_name is the canonical display name (uppercase for props/locations).
- entity_type must be one of: {entity_types} (use lowercase).
- confidence is 0..1.
- Do not invent node_ids; only use ids present in the scene text.

Respond with ONLY a single JSON object, no markdown, no code fences, in this exact shape:
{{"candidates": [{{"node_id": "...", "matched_text": "...", "entity_type": "...", "suggested_name": "...", "confidence": 0.0}}]}}
If there are no candidates, return {{"candidates": []}}.

Scene text blocks:
{node_lines}
"""

_PRICE_IN_PER_TOKEN = 3.0 / 1_000_000
_PRICE_OUT_PER_TOKEN = 15.0 / 1_000_000

_TEXT_TYPES = {"action", "dialogue", "parenthetical", "shot", "general"}


def collect_text_nodes(content: dict) -> list[dict]:
    """Return [{node_id, type, text}] for every text-block node in the fragment."""
    nodes: list[dict] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") in _TEXT_TYPES:
            attrs = node.get("attrs") or {}
            node_id = attrs.get("id")
            if isinstance(node_id, str) and node_id:
                nodes.append(
                    {"node_id": node_id, "type": node["type"], "text": node_text(node)}
                )
        for child in node.get("content", []) or []:
            walk(child)

    walk(content)
    return nodes


def resolve_span(text: str, needle: str, occupied: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Find the first occurrence of needle in text that does not overlap `occupied`."""
    needle_l = needle.lower()
    text_l = text.lower()
    start = 0
    while True:
        idx = text_l.find(needle_l, start)
        if idx == -1:
            return None
        end = idx + len(needle)
        if not any(s < end and idx < e for (s, e) in occupied):
            return (idx, end)
        start = idx + 1


def normalize_candidate(raw: dict) -> dict | None:
    """Validate/clamp a raw model candidate; return None if unusable."""
    node_id = str(raw.get("node_id") or "").strip()
    matched_text = str(raw.get("matched_text") or "").strip()
    suggested_name = str(raw.get("suggested_name") or "").strip()
    entity_type = str(raw.get("entity_type") or "")
    if entity_type not in ENTITY_TYPES or not node_id or not matched_text or not suggested_name:
        return None
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))
    return {
        "node_id": node_id,
        "matched_text": matched_text,
        "entity_type": entity_type,  # Keep as string for validation, convert to Enum later
        "suggested_name": suggested_name,
        "confidence": confidence,
    }


def _parse_json_candidates(content: str) -> list[dict]:
    """Parse a model's JSON reply into a candidates list. Empty on failure."""
    if not content:
        return []
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json")
        text = text.strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []
    return candidates


async def _call_model(client: AsyncOpenAI, nodes: list[dict]) -> list[dict]:
    node_lines = "\n".join(f"[{n['node_id']}] {n['text']}" for n in nodes)
    prompt = _AI_PROMPT.format(node_lines=node_lines, entity_types=", ".join(ENTITY_TYPES))
    resp = await client.chat.completions.create(
        model=NVIDIA_MODEL,
        max_tokens=2048,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    usage = getattr(resp, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) or 0
    tokens_out = getattr(usage, "completion_tokens", 0) or 0
    cost = tokens_in * _PRICE_IN_PER_TOKEN + tokens_out * _PRICE_OUT_PER_TOKEN
    record_ai_call("extract entities from scene text blocks", NVIDIA_MODEL, cost, tokens_in, tokens_out)


    message = getattr(resp, "choices", None) or [{}]
    content = (message[0].message.content if getattr(message[0], "message", None) else None) or ""
    print("resp:", message)
    candidates = _parse_json_candidates(content)
    record_ai_result({"candidates": candidates})
    return candidates


async def _match_entity(db: AsyncSession, project_id: UUID, entity_type: str, name: str) -> models.Entity | None:
    # Convert lowercase string entity_type to uppercase EntityType enum
    entity_type_enum = EntityType(entity_type.lower())
    sim = func.similarity(models.Entity.canonical_name, name)
    return await db.scalar(
        select(models.Entity)
        .where(
            models.Entity.project_id == project_id,
            models.Entity.entity_type == entity_type_enum,
            sim > 0.6,
        )
        .order_by(sim.desc())
        .limit(1)
    )


async def run_ai_suggestions(
    settings,
    db: AsyncSession,
    scene: models.Scene,
    client: AsyncOpenAI | None = None,
) -> list[models.AiSuggestion]:
    """Run full extraction for a scene. Short-circuits when the hash is unchanged."""
    # if scene.last_ai_hash == scene.content_hash:
    #     result = await db.execute(
    #         select(models.AiSuggestion)
    #         .where(models.AiSuggestion.scene_id == scene.id)
    #         .order_by(models.AiSuggestion.created_at)
    #     )
    #     return list(result.scalars().all())

    nodes = collect_text_nodes(scene.content)
    if not nodes:
        scene.last_ai_hash = scene.content_hash
        return []

    screenplay = await db.get(models.Screenplay, scene.screenplay_id)
    if not screenplay:
        return []

    client = client or AsyncOpenAI(base_url=settings.ai_base_url, api_key=settings.nvidia_api_key)
    candidates = await _call_model(client, nodes)
    print("candidates:", candidates)

    existing = await db.execute(
        select(models.AiSuggestion).where(models.AiSuggestion.scene_id == scene.id)
    )
    existing_keys = {(s.node_id, s.start_offset, s.end_offset) for s in existing.scalars().all()}

    nodes_by_id = {n["node_id"]: n for n in nodes}
    used_spans: dict[str, list[tuple[int, int]]] = {}
    created: list[models.AiSuggestion] = []

    for raw in candidates:
        parsed = normalize_candidate(raw)
        if not parsed or parsed["node_id"] not in nodes_by_id:
            continue
        node = nodes_by_id[parsed["node_id"]]
        span = resolve_span(node["text"], parsed["matched_text"], used_spans.setdefault(node["node_id"], []))
        start, end = span if span else (None, None)
        if (parsed["node_id"], start, end) in existing_keys:
            continue
        if span:
            used_spans[node["node_id"]].append(span)

        matched = await _match_entity(db, screenplay.project_id, parsed["entity_type"], parsed["suggested_name"])
        # Convert lowercase string entity_type to uppercase EntityType enum for database
        suggested_type_enum = EntityType(parsed["entity_type"].lower())
        suggestion = models.AiSuggestion(
            scene_id=scene.id,
            node_id=parsed["node_id"],
            matched_text=parsed["matched_text"],
            start_offset=start,
            end_offset=end,
            suggested_type=suggested_type_enum,
            suggested_name=parsed["suggested_name"],
            matched_entity_id=matched.id if matched else None,
            confidence=parsed["confidence"],
            model=NVIDIA_MODEL,
            prompt_version=PROMPT_VERSION,            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    scene.last_ai_hash = scene.content_hash
    await db.flush()
    return created


async def auto_analyze(settings, sessionmaker, scene_id: UUID) -> None:
    """Background auto-analysis after scene creation. Best-effort, never fails the request."""
    if not settings.nvidia_api_key:
        return
    async with sessionmaker() as db:
        scene = await db.get(models.Scene, scene_id)
        if not scene:
            return
        try:
            await run_ai_suggestions(settings, db, scene)
            await db.commit()
        except Exception:
            await db.rollback()
            raise