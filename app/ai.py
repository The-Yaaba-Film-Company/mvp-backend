import json
from uuid import UUID

from google.genai import Client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.content_validator import node_text
from app.models import EntityType
from app.schema import ENTITY_TYPES
from app.sentry import record_ai_call, record_ai_result

GEMINI_MODEL = "gemini-3.6-flash"
PROMPT_VERSION = "1"

_AI_PROMPT = """
ROLE

You are a professional script supervisor and script breakdown specialist —
the kind of person a production office hires to turn a shooting script into
department-ready breakdown sheets for props, wardrobe, vehicles, sound,
VFX/SFX, camera, and cast. You have done this for ads, music videos, and
feature films. You are not being asked to write, improve, or evaluate the
script. You are being asked to read it the way a breakdown artist does:
line by line, flagging anything a department head would need to know about,
and nothing else.

TASK

You will be given one screenplay scene, already split into nodes by the
editor. The CONTEXT block tells you what the scene's Scene Heading and
Character cues have already established structurally — its location and
its named speaking characters. That context exists so you don't waste a
candidate re-stating something the editor already knows.

Everything else is your job to find. Read every free-text node (action,
dialogue, parenthetical, general) and flag every mention of anything a
production department would need to track: characters, locations, props,
wardrobe/costume items, vehicles, set dressing, sound cues, VFX/SFX,
makeup/hair, stunts, animals, background extras, equipment, and camera
setups (shot framing/movement — including camera language embedded in
action text, e.g. "we push in on," "close on," even when it isn't a formal
Shot node).

Character and location are not special cases — treat a name or place that
shows up only in free text, with no Character cue and no mention in the
Scene Heading, exactly the way you'd treat an unlisted prop: it's real, the
production needs to know about it, flag it. This happens constantly — a
background character described only in action ("a FISHERMAN watches from
the shore"), a second location referenced mid-scene (a car interior named
inside an exterior scene, a place mentioned only in dialogue), a name
dropped once and never given a formal cue. Anything already in CONTEXT is
covered and should not be repeated; anything not in CONTEXT is fair game,
regardless of what type of element it is.

For each mention, output one candidate: which node it's in, the exact
matched text, what type of element it is, a clean canonical name for it,
and your confidence.

You are not to search anything, look anything up, rewrite any part of the
script, or comment on story, dialogue quality, or formatting. Extraction
only.

EXAMPLE

Input:

  SCENE_ID: scene_beach_01

  CONTEXT (already tracked — do not re-suggest):
  LOCATION: BEACH
  CHARACTERS: JAMES, SHADOWY FIGURE

  NODES:
  [id=a1 type=action] JAMES scans the horizon.
  [id=a2 type=action] Then a flicker of movement across the beach. A SHADOWY FIGURE emerges from the darkness, face obscured beneath a widebrimmed hat.
  [id=a3 type=action] James' eyebrows knot, his mouth opening to speak. No words come out.
  [id=a4 type=action] The shadowy figure moves closer, a trench coat now visible, hanging from its shoulders. It's taking its time.
  [id=a5 type=action] James steps back as the figure reaches him, sliding a hand into a coat pocket.
  [id=a6 type=action] We focus on the figure's pocket.
  [id=a7 type=action] Eyes widening, James forces himself to take a step closer. He watches the figure's hand closely as it pulls out a small envelope of photographs.
  [id=a8 type=action] James frowns, confused as he takes the envelope. He opens it to reveal a set of photographs. His face falls as his eyes move back into the concealed face of the shadowy figure.
  [id=a9 type=action] James' corpse lies face down on the beach, the photos scattered around him in the sand.
  [id=g1 type=general] CHYRON: The Beach
  [id=g2 type=general] TITLE: Time of death - 15:00
  [id=g3 type=general] SUPER: Cause of death - Unknown

Expected output:

{{"candidates": [
  {{"node_id": "a2", "matched_text": "widebrimmed hat", "entity_type": "wardrobe", "suggested_name": "WIDE-BRIMMED HAT", "confidence": 0.8}},
  {{"node_id": "a4", "matched_text": "trench coat", "entity_type": "wardrobe", "suggested_name": "TRENCH COAT", "confidence": 0.9}},
  {{"node_id": "a6", "matched_text": "We focus on the figure's pocket.", "entity_type": "camera_setup", "suggested_name": "INSERT - FIGURE'S POCKET", "confidence": 0.55}},
  {{"node_id": "a7", "matched_text": "envelope of photographs", "entity_type": "prop", "suggested_name": "PHOTOGRAPHS", "confidence": 0.85}},
  {{"node_id": "a8", "matched_text": "a set of photographs", "entity_type": "prop", "suggested_name": "PHOTOGRAPHS", "confidence": 0.8}},
  {{"node_id": "a9", "matched_text": "the photos", "entity_type": "prop", "suggested_name": "PHOTOGRAPHS", "confidence": 0.75}},
  {{"node_id": "g1", "matched_text": "CHYRON: The Beach", "entity_type": "vfx", "suggested_name": "CHYRON - THE BEACH", "confidence": 0.9}},
  {{"node_id": "g2", "matched_text": "TITLE: Time of death - 15:00", "entity_type": "vfx", "suggested_name": "TITLE - TIME OF DEATH: 15:00", "confidence": 0.9}},
  {{"node_id": "g3", "matched_text": "SUPER: Cause of death - Unknown", "entity_type": "vfx", "suggested_name": "SUPER - CAUSE OF DEATH: UNKNOWN", "confidence": 0.9}}
]}}

Notice what is deliberately absent: JAMES and SHADOWY FIGURE are not
re-suggested as characters (already in context), BEACH is not re-suggested
as a location (already in context), and "coat pocket" in a5 is not
double-tagged as a separate prop once the coat itself is already flagged in
a4 — a breakdown artist doesn't tag the same wardrobe item twice under two
names.

A SECOND, SHORTER EXAMPLE — new characters and locations not in context:

Input:

  SCENE_ID: scene_diner_04

  CONTEXT (already tracked — do not re-suggest):
  LOCATION: DINER
  CHARACTERS: MARA

  NODES:
  [id=a1 type=action] MARA slides into a booth. Through the window, across the street, a MAN IN A GRAY COAT watches from beside a payphone outside the OLD BUS DEPOT.

Expected output:

{{"candidates": [
  {{"node_id": "a1", "matched_text": "MAN IN A GRAY COAT", "entity_type": "character", "suggested_name": "MAN IN GRAY COAT", "confidence": 0.7}},
  {{"node_id": "a1", "matched_text": "OLD BUS DEPOT", "entity_type": "location", "suggested_name": "BUS DEPOT", "confidence": 0.65}},
  {{"node_id": "a1", "matched_text": "payphone", "entity_type": "prop", "suggested_name": "PAYPHONE", "confidence": 0.6}}
]}}

MARA and DINER are skipped (already in CONTEXT), but the man watching from
across the street and the second location he's standing near are flagged
exactly like the payphone prop — none of the three has a formal Character
cue or Scene Heading, but all three are real things a department needs to
know about.

CONSTRAINTS

- matched_text must be an exact, verbatim substring of that node's given
  text — same characters, same case, same spelling (including any typos
  in the source). Do not correct, normalize, or paraphrase matched_text.
- suggested_name is the canonical display name: your own clean, corrected,
  uppercase label for the entity (e.g. "widebrimmed hat" as matched_text
  can still have suggested_name "WIDE-BRIMMED HAT"). Use the exact same
  suggested_name every time the same real-world thing recurs across nodes,
  even under different phrasing — this is what lets the system recognize
  repeat mentions as one entity instead of duplicates.
- entity_type must be exactly one of: {entity_types} (lowercase, no others,
  no inventing new categories — use "other" if genuinely nothing fits).
- confidence is a number from 0 to 1. Use 0.8-1.0 for explicit, unambiguous
  mentions; 0.5-0.79 for clear but implicit mentions (e.g. camera language
  embedded in action rather than a formal Shot node); below 0.5 only for
  genuine judgment calls. Omit anything you would rate below 0.3 rather
  than emit a low-value guess.
- Only use node_id values that appear in the given NODES block. Never
  invent, guess, or reuse a node_id for text that isn't actually in that
  node.
- Do not re-suggest anything already listed in CONTEXT (the scene's known
  location or known characters, whether stated verbatim or by an obvious
  alias like "the shadowy figure" for SHADOWY FIGURE). Do actively suggest
  a new character or location the moment free text introduces one that
  isn't in CONTEXT — entity_type "character" and "location" get exactly
  the same treatment as every other type, not skipped by default.
- Do not tag sub-parts of something you've already tagged as the same
  entity (a pocket on an already-tagged coat, a strap on an already-tagged
  bag) unless it is independently relevant to a department.
- One candidate per distinct mention. If a real-world thing is mentioned
  three times across three nodes, emit three candidates (one per node),
  not one merged candidate — downstream logic handles merging.
- Perform no other action: no web search, no rewriting the script, no
  commentary on story or dialogue quality, no formatting suggestions.
- Respond with ONLY a single JSON object, no markdown, no code fences, no
  preamble or explanation, in exactly this shape:
  {{"candidates": [{{"node_id": "...", "matched_text": "...", "entity_type": "...", "suggested_name": "...", "confidence": 0.0}}]}}
  If there are no candidates, return {{"candidates": []}}.
"""

_PRICE_IN_PER_TOKEN = 0.75 / 1_000_000
_PRICE_OUT_PER_TOKEN = 3.75 / 1_000_000

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


async def _call_model(client: Client, nodes: list[dict], scene: models.Scene) -> list[dict]:
    node_lines = "\n".join(f"[{n['node_id']}] {n['text']}" for n in nodes)
    prompt = _AI_PROMPT.format(entity_types=", ".join(ENTITY_TYPES))
    input_text = (
        f"SCENE_ID: {scene.id}\n\n"
        f"NODES:\n{node_lines}"
    )
    resp = client.interactions.create(
        model=GEMINI_MODEL,
        system_instruction=prompt,
        generation_config={"temperature": 0.2, "thinking_level": "medium"},
        input=input_text,
    )
    usage = resp.usage
    tokens_in = usage.total_input_tokens
    tokens_out = usage.total_output_tokens
    cost = tokens_in * _PRICE_IN_PER_TOKEN + tokens_out * _PRICE_OUT_PER_TOKEN
    record_ai_call("extract entities from scene text blocks", GEMINI_MODEL, cost, tokens_in, tokens_out)

    message = resp.output_text or ""
    candidates = _parse_json_candidates(message)
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
    client: Client | None = None,
) -> list[models.AiSuggestion]:
    """Run full extraction for a scene. Short-circuits when the hash is unchanged."""
    if not settings.gemini_api_key:
        return []

    if scene.last_ai_hash == scene.content_hash:
        result = await db.execute(
            select(models.AiSuggestion)
            .where(models.AiSuggestion.scene_id == scene.id)
            .order_by(models.AiSuggestion.created_at)
        )
        return list(result.scalars().all())

    nodes = collect_text_nodes(scene.content)
    if not nodes:
        scene.last_ai_hash = scene.content_hash
        return []

    screenplay = await db.get(models.Screenplay, scene.screenplay_id)
    if not screenplay:
        return []

    client = client or Client(api_key=settings.gemini_api_key)
    candidates = await _call_model(client, nodes, scene)

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
            model=GEMINI_MODEL,
            prompt_version=PROMPT_VERSION,
            status="pending",
        )
        db.add(suggestion)
        created.append(suggestion)

    scene.last_ai_hash = scene.content_hash
    await db.flush()
    return created


async def auto_analyze(settings, sessionmaker, scene_id: UUID) -> None:
    """Background auto-analysis after scene creation. Best-effort, never fails the request."""
    if not settings.gemini_api_key:
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