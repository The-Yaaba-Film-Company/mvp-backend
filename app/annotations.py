from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import models
from app.content_validator import node_text
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import (
    AnnotationListResponse,
    AnnotationOut,
    CreateAnnotationRequest,
)
from app.sentry import breadcrumb

# Router for scene-scoped annotation operations
scene_annotations_router = APIRouter(prefix="/scenes/{scene_id}/annotations", tags=["annotations"])


def _annotation_out(ann: models.Annotation) -> AnnotationOut:
    return AnnotationOut(
        id=ann.id,
        scene_id=ann.scene_id,
        node_id=ann.node_id,
        start_offset=ann.start_offset,
        end_offset=ann.end_offset,
        entity_id=ann.entity_id,
        source=ann.source,
        created_by=ann.created_by,
        created_at=ann.created_at,
    )


@scene_annotations_router.get("", response_model=AnnotationListResponse)
async def list_annotations(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Scene)
        .options(selectinload(models.Scene.screenplay))
        .where(models.Scene.id == scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role("viewer")(scene.screenplay.project_id, principal, db)

    result = await db.execute(
        select(models.Annotation)
        .where(models.Annotation.scene_id == scene_id)
        .order_by(models.Annotation.created_at)
    )
    annotations = result.scalars().all()
    breadcrumb("db", "list annotations", scene_id=str(scene_id), count=len(annotations))
    return AnnotationListResponse(items=[_annotation_out(a) for a in annotations])


@scene_annotations_router.post("", status_code=201, response_model=AnnotationOut)
async def create_annotation(
    scene_id: UUID,
    payload: CreateAnnotationRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Scene)
        .options(selectinload(models.Scene.screenplay))
        .where(models.Scene.id == scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role("editor")(scene.screenplay.project_id, principal, db)

    # Validate span
    scene_content = scene.content
    if not isinstance(scene_content, dict):
        raise HTTPException(status_code=422, detail="Invalid scene content.")

    # Find the node
    node_text = _get_node_text(scene_content, payload.node_id)
    if node_text is None:
        raise HTTPException(status_code=422, detail="Node not found or not a text block.")

    if payload.start_offset < 0 or payload.end_offset > len(node_text):
        raise HTTPException(status_code=422, detail="Offsets out of bounds.")

    entity = await db.get(models.Entity, payload.entity_id)
    if not entity or entity.project_id != scene.screenplay.project_id:
        raise HTTPException(status_code=404, detail="Entity not found.")

    annotation = models.Annotation(
        scene_id=scene_id,
        node_id=payload.node_id,
        start_offset=payload.start_offset,
        end_offset=payload.end_offset,
        entity_id=payload.entity_id,
        source="manual",
        created_by=principal.user_id,
    )
    db.add(annotation)
    await db.flush()

    # Rebuild scene_entities for this scene
    from app.scenes import _rebuild_scene_entities
    await _rebuild_scene_entities(db, scene_id)

    await db.commit()
    await db.refresh(annotation)

    return _annotation_out(annotation)


# Router for direct annotation operations (by ID)
annotations_router = APIRouter(prefix="/annotations", tags=["annotations"])


@annotations_router.delete("/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    ann = await db.get(models.Annotation, annotation_id)
    if not ann:
        raise HTTPException(status_code=404, detail="Annotation not found.")

    result = await db.execute(
        select(models.Scene)
        .options(selectinload(models.Scene.screenplay))
        .where(models.Scene.id == ann.scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role("editor")(scene.screenplay.project_id, principal, db)

    await db.delete(ann)
    await db.commit()

    # Rebuild scene_entities for this scene
    from app.scenes import _rebuild_scene_entities
    await _rebuild_scene_entities(db, ann.scene_id)


def _get_node_text(content: dict, node_id: str) -> str | None:
    """Extract text from a node by its ID, ensuring it's a text block type."""
    if not isinstance(content, dict):
        return None

    def walk(node: dict) -> str | None:
        if not isinstance(node, dict):
            return None
        attrs = node.get("attrs") or {}
        if attrs.get("id") == node_id:
            # Check if it's a text block type
            text_types = {"action", "dialogue", "parenthetical", "shot", "general"}
            if node.get("type") in text_types:
                return node_text(node)
            return None
        for child in node.get("content", []) or []:
            result = walk(child)
            if result is not None:
                return result
        return None

    return walk(content)