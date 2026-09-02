from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, Scene, Screenplay, SceneEntity, SceneMetrics, EntityType
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import (
    CharacterReport,
    EntityReport,
    LocationReport,
    PaginationReport,
    RuntimeReport,
    SceneMetrics as SceneMetricsSchema,
)
from app.sentry import breadcrumb

# Router for project-scoped reports
project_reports_router = APIRouter(prefix="/projects/{project_id}/reports", tags=["reports"])


@project_reports_router.get("/characters", response_model=list[CharacterReport])
async def characters_report(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    db: AsyncSession = Depends(get_db),
):
    """Get character entities with their dialogue counts and scene_ids."""
    # Get all character entities for this project
    result = await db.execute(
        select(Entity)
        .where(
            Entity.project_id == project_id,
            Entity.entity_type == EntityType.character,
        )
        .order_by(Entity.canonical_name)
    )
    characters = result.scalars().all()

    # For each character, find scenes where they speak (have dialogue nodes)
    char_reports = []
    for char in characters:
        # Find scenes with this character's dialogue
        scenes_result = await db.execute(
            select(Scene.id)
            .join(Screenplay, Scene.screenplay_id == Screenplay.id)
            .where(
                Screenplay.project_id == project_id,
                Scene.content.op('->>')('content').isnot(None),
            )
        )
        scene_ids = [row[0] for row in scenes_result.all()]

        # Count dialogue nodes for this character
        dialogue_count = 0
        char_scene_ids = []
        for scene_id in scene_ids:
            scene = await db.get(Scene, scene_id)
            if scene and scene.content:
                count = _count_character_dialogue(scene.content, str(char.id))
                if count > 0:
                    dialogue_count += count
                    char_scene_ids.append(scene_id)

        char_reports.append(
            CharacterReport(
                id=char.id,
                canonical_name=char.canonical_name,
                aliases=char.aliases,
                scene_ids=char_scene_ids,
                dialogue_count=dialogue_count,
            )
        )

    breadcrumb("db", "characters report", project_id=str(project_id), count=len(char_reports))
    return char_reports


def _count_character_dialogue(content: dict, character_id: str) -> int:
    """Count dialogue nodes that follow a character node with the given ID."""
    count = 0
    if not isinstance(content, dict):
        return 0

    # Walk the content tree
    nodes = content.get("content", []) or []
    for i, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get("type") != "dialogue":
            continue
        # Check if previous node is a character with matching ID
        if i > 0:
            prev_node = nodes[i - 1]
            if isinstance(prev_node, dict) and prev_node.get("type") == "character":
                attrs = prev_node.get("attrs", {})
                if attrs.get("characterId") == character_id:
                    count += 1
    return count


@project_reports_router.get("/locations", response_model=list[LocationReport])
async def locations_report(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    db: AsyncSession = Depends(get_db),
):
    """Get location entities with their scene_ids."""
    # Get location entities with scene_ids from scene_entities (which includes scene.location_entity_id)

    result = await db.execute(
        select(
            Entity,
            func.array_agg(Scene.id.distinct()).label("scene_ids"),
        )
        .join(SceneEntity, Entity.id == SceneEntity.entity_id)
        .join(Scene, SceneEntity.scene_id == Scene.id)
        .join(Screenplay, Scene.screenplay_id == Screenplay.id)
        .where(
            Screenplay.project_id == project_id,
            Entity.entity_type == EntityType.location,
        )
        .group_by(Entity.id)
        .order_by(Entity.canonical_name)
    )

    rows = result.all()
    breadcrumb("db", "locations report", project_id=str(project_id), count=len(rows))

    return [
        LocationReport(
            id=entity.id,
            canonical_name=entity.canonical_name,
            aliases=entity.aliases,
            scene_ids=scene_ids,
        )
        for entity, scene_ids in rows
    ]


@project_reports_router.get("/entities", response_model=list[EntityReport])
async def entities_report(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    db: AsyncSession = Depends(get_db),
):
    """Get all non-character/location entities with occurrence counts and scene_ids."""

    result = await db.execute(
        select(
            Entity,
            func.sum(SceneEntity.occurrence_count).label("occurrence_count"),
            func.array_agg(SceneEntity.scene_id.distinct()).label("scene_ids"),
        )
        .join(SceneEntity, Entity.id == SceneEntity.entity_id)
        .join(Scene, SceneEntity.scene_id == Scene.id)
        .join(Screenplay, Scene.screenplay_id == Screenplay.id)
        .where(
            Screenplay.project_id == project_id,
            Entity.entity_type.not_in([EntityType.character, EntityType.location]),
        )
        .group_by(Entity.id)
        .order_by(Entity.entity_type, Entity.canonical_name)
    )

    rows = result.all()
    breadcrumb("db", "entities report", project_id=str(project_id), count=len(rows))

    return [
        EntityReport(
            id=entity.id,
            entity_type=entity.entity_type.value,
            canonical_name=entity.canonical_name,
            aliases=entity.aliases,
            scene_ids=scene_ids,
            occurrence_count=occurrence_count or 0,
        )
        for entity, occurrence_count, scene_ids in rows
    ]


# Router for screenplay-scoped reports
screenplay_reports_router = APIRouter(prefix="/screenplays/{screenplay_id}/reports", tags=["reports"])


@screenplay_reports_router.get("/runtime", response_model=RuntimeReport)
async def runtime_report(
    screenplay_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    await require_project_role("viewer")(sp.project_id, principal, db)

    # Estimate runtime: 1 page per minute, using scene metrics if available
    result = await db.execute(
        select(func.coalesce(func.sum(SceneMetrics.page_length), 0)).where(
            SceneMetrics.screenplay_id == screenplay_id
        )
    )
    total_pages = result.scalar() or 0
    runtime = int(total_pages) if total_pages > 0 else 0

    # Fallback: estimate from scene count (roughly 1 page per scene)
    if runtime == 0:
        scene_count = await db.scalar(
            select(func.count(Scene.id)).where(Scene.screenplay_id == screenplay_id)
        )
        runtime = scene_count or 0

    breadcrumb("db", "runtime report", screenplay_id=str(screenplay_id), runtime=runtime)
    return RuntimeReport(runtime_minutes=runtime)


@screenplay_reports_router.get("/pagination", response_model=PaginationReport)
async def pagination_report(
    screenplay_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    await require_project_role("viewer")(sp.project_id, principal, db)

    result = await db.execute(
        select(SceneMetrics)
        .where(SceneMetrics.screenplay_id == screenplay_id)
        .order_by(SceneMetrics.start_page)
    )
    metrics = result.scalars().all()

    if not metrics:
        # Return empty report if no metrics cached
        breadcrumb("db", "pagination report", screenplay_id=str(screenplay_id), page_count=0)
        return PaginationReport(page_count=0, runtime_minutes=0, scene_metrics=[])

    page_count = max(m.end_page for m in metrics)
    total_length = sum(m.page_length for m in metrics)
    runtime = int(total_length) if total_length > 0 else page_count

    breadcrumb("db", "pagination report", screenplay_id=str(screenplay_id), page_count=page_count)
    return PaginationReport(
        page_count=page_count,
        runtime_minutes=runtime,
        scene_metrics=[
            SceneMetricsSchema(
                scene_id=m.scene_id,
                start_page=m.start_page,
                end_page=m.end_page,
                page_length=m.page_length,
            )
            for m in metrics
        ],
    )


@screenplay_reports_router.patch("/pagination", response_model=PaginationReport)
async def update_pagination_report(
    screenplay_id: UUID,
    payload: PaginationReport,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    await require_project_role("editor")(sp.project_id, principal, db)

    # Upsert scene_metrics - overwrite completely (non-authoritative cache)

    # Delete existing metrics for this screenplay
    await db.execute(
        delete(SceneMetrics).where(SceneMetrics.screenplay_id == screenplay_id)
    )

    # Insert new metrics
    for m in payload.scene_metrics:
        db.add(
            SceneMetrics(
                screenplay_id=screenplay_id,
                scene_id=m.scene_id,
                start_page=m.start_page,
                end_page=m.end_page,
                page_length=m.page_length,
            )
        )

    await db.commit()
    breadcrumb("db", "update pagination", screenplay_id=str(screenplay_id), page_count=payload.page_count)

    return payload