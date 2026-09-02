from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.content_validator import extract_heading_info, node_text
from app.deps import get_db, require_project_role
from app.models import EntityType
from app.schema import ENTITY_TYPES, SearchResult, SearchResultListResponse
from app.sentry import breadcrumb

search_router = APIRouter(prefix="/projects/{project_id}/search", tags=["search"])

TEXT_BLOCK_TYPES = ("action", "dialogue", "parenthetical", "shot", "general")
SNIPPET_CONTEXT = 24


@search_router.get("", response_model=SearchResultListResponse)
async def project_search(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    q: str | None = Query(None),
    types: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = (q or "").strip()
    if not query:
        return SearchResultListResponse(items=[])

    type_filter: set[str] = set()
    if types:
        type_filter = {t.strip() for t in types.split(",") if t.strip() in ENTITY_TYPES}

    items: list[SearchResult] = []
    query_lower = query.lower()

    entities = await _find_entities(db, project_id, query_lower, type_filter)
    if not type_filter:
        scenes = await _project_scenes(db, project_id)
        items = _scene_results(scenes, query, query_lower)
        items.extend(_text_results(scenes, query, query_lower))

    scene_map = await _entity_scene_map(db, [e.id for e in entities])
    for entity in entities:
        scene_id = scene_map.get(entity.id)
        kind = (
            "character"
            if entity.entity_type == EntityType.character
            else "location"
            if entity.entity_type == EntityType.location
            else "entity"
        )
        items.append(
            SearchResult(
                id=str(entity.id),
                kind=kind,
                match=entity.canonical_name,
                snippet="",
                scene_id=scene_id,
                node_id=None,
                start_offset=None,
                end_offset=None,
            )
        )

    breadcrumb(
        "db", "project search", project_id=str(project_id), q=query, count=len(items)
    )
    return SearchResultListResponse(items=items[:50])


# --- entity results ---


async def _find_entities(
    db: AsyncSession, project_id: UUID, query_lower: str, type_filter: set[str]
) -> list[models.Entity]:
    stmt = select(models.Entity).where(
        models.Entity.project_id == project_id,
        or_(
            models.Entity.canonical_name.ilike(f"%{query_lower}%"),
            func.array_to_string(models.Entity.aliases, " ").ilike(f"%{query_lower}%"),
        ),
    )
    if type_filter:
        # Convert lowercase string type_filter to uppercase EntityType enum
        enum_filter = [EntityType(t.lower()) for t in sorted(type_filter)]
        stmt = stmt.where(models.Entity.entity_type.in_(enum_filter))
    stmt = stmt.order_by(models.Entity.canonical_name).limit(30)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _entity_scene_map(
    db: AsyncSession, entity_ids: list[UUID]
) -> dict[UUID, UUID]:
    if not entity_ids:
        return {}
    result = await db.execute(
        select(models.SceneEntity.entity_id, models.SceneEntity.scene_id)
        .join(models.Scene, models.Scene.id == models.SceneEntity.scene_id)
        .where(models.SceneEntity.entity_id.in_(entity_ids))
        .order_by(models.Scene.order_key)
    )
    scene_map: dict[UUID, UUID] = {}
    for entity_id, scene_id in result.all():
        scene_map.setdefault(entity_id, scene_id)
    return scene_map


# --- scene + text results ---


async def _project_scenes(db: AsyncSession, project_id: UUID) -> list[models.Scene]:
    result = await db.execute(
        select(models.Scene)
        .join(models.Screenplay, models.Scene.screenplay_id == models.Screenplay.id)
        .where(models.Screenplay.project_id == project_id)
        .order_by(models.Scene.order_key, models.Scene.created_at)
    )
    return list(result.scalars().all())


def _heading_text(scene: models.Scene) -> str:
    info = extract_heading_info(scene.content) if isinstance(scene.content, dict) else {}
    int_ext = info.get("int_ext") or (scene.int_ext.value if scene.int_ext else None)
    location = info.get("location")
    time_of_day = info.get("time_of_day") or scene.time_of_day
    parts = [p for p in (int_ext, location) if p]
    heading = " ".join(parts) or "Untitled Scene"
    return f"{heading} - {time_of_day}" if time_of_day else heading


def _scene_results(scenes: list[models.Scene], query: str, query_lower: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    for scene in scenes:
        heading = _heading_text(scene)
        number = scene.number or ""
        if query_lower in heading.lower() or (number and query_lower in number.lower()):
            results.append(
                SearchResult(
                    id=str(scene.id),
                    kind="scene",
                    match=heading,
                    snippet="",
                    scene_id=scene.id,
                    node_id=None,
                    start_offset=None,
                    end_offset=None,
                )
            )
    return results


def _text_results(
    scenes: list[models.Scene], query: str, query_lower: str
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for scene in scenes:
        content = scene.content
        if not isinstance(content, dict):
            continue
        for node in content.get("content", []) or []:
            if not isinstance(node, dict) or node.get("type") not in TEXT_BLOCK_TYPES:
                continue
            text = node_text(node)
            if not isinstance(text, str) or not text:
                continue
            match_start = text.lower().find(query_lower)
            if match_start < 0:
                continue
            match_end = match_start + len(query)
            node_id = _node_id(node)
            if not node_id:
                continue
            results.append(
                SearchResult(
                    id=f"{scene.id}:{node_id}",
                    kind="text",
                    match=text[match_start:match_end],
                    snippet=_snippet(text, match_start, match_end),
                    scene_id=scene.id,
                    node_id=node_id,
                    start_offset=match_start,
                    end_offset=match_end,
                )
            )
    return results


def _node_id(node: dict) -> str:
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    node_id = attrs.get("id")
    return node_id if isinstance(node_id, str) else ""


def _snippet(text: str, start: int, end: int) -> str:
    lo = max(0, start - SNIPPET_CONTEXT)
    hi = min(len(text), end + SNIPPET_CONTEXT)
    head = "" if lo <= 0 else "..."
    tail = "" if hi >= len(text) else "..."
    return f"{head}{text[lo:start]}{text[start:end]}{text[end:hi]}{tail}"