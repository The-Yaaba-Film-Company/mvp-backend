from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import models
from app.ai import run_ai_suggestions
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import AiSuggestionListResponse, AiSuggestionOut
from app.sentry import breadcrumb


def _suggestion_out(s: models.AiSuggestion) -> AiSuggestionOut:
    return AiSuggestionOut(
        id=s.id,
        scene_id=s.scene_id,
        node_id=s.node_id,
        matched_text=s.matched_text,
        start_offset=s.start_offset,
        end_offset=s.end_offset,
        suggested_type=s.suggested_type.value if s.suggested_type else None,
        suggested_name=s.suggested_name,
        matched_entity_id=s.matched_entity_id,
        confidence=float(s.confidence) if s.confidence is not None else None,
        model=s.model,
        prompt_version=s.prompt_version,
        status=s.status,
        reviewed_by=s.reviewed_by,
        reviewed_at=s.reviewed_at,
        created_at=s.created_at,
    )


async def _get_scene_with_auth(
    scene_id: UUID, principal: AuthPrincipal, db: AsyncSession, required_role: str
) -> models.Scene:
    result = await db.execute(
        select(models.Scene)
        .options(selectinload(models.Scene.screenplay))
        .where(models.Scene.id == scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role(required_role)(scene.screenplay.project_id, principal, db)
    return scene


async def _get_suggestion_with_auth(
    suggestion_id: UUID, principal: AuthPrincipal, db: AsyncSession, required_role: str
) -> models.AiSuggestion:
    suggestion = await db.get(models.AiSuggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    scene = await _get_scene_with_auth(suggestion.scene_id, principal, db, required_role)
    suggestion._scene = scene
    return suggestion


scene_ai_router = APIRouter(prefix="/scenes/{scene_id}/ai-suggestions", tags=["ai-suggestions"])
scene_ai_run_router = APIRouter(prefix="/scenes/{scene_id}", tags=["ai-suggestions"])
suggestions_router = APIRouter(prefix="/ai-suggestions", tags=["ai-suggestions"])


@scene_ai_run_router.post("/ai-suggest", response_model=AiSuggestionListResponse)
async def run_ai_analysis(
    scene_id: UUID,
    request: Request,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "editor")
    settings = request.app.state.settings
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="AI not configured.")

    created = await run_ai_suggestions(settings, db, scene)

    await db.commit()
    for s in created:
        await db.refresh(s)
    return AiSuggestionListResponse(items=[_suggestion_out(s) for s in created])


@scene_ai_router.get("", response_model=AiSuggestionListResponse)
async def list_suggestions(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    await _get_scene_with_auth(scene_id, principal, db, "viewer")
    result = await db.execute(
        select(models.AiSuggestion)
        .where(models.AiSuggestion.scene_id == scene_id)
        .order_by(models.AiSuggestion.created_at)
    )
    suggestions = result.scalars().all()
    breadcrumb("db", "list ai suggestions", scene_id=str(scene_id), count=len(suggestions))
    return AiSuggestionListResponse(items=[_suggestion_out(s) for s in suggestions])


@suggestions_router.post("/{suggestion_id}/accept", response_model=AiSuggestionOut)
async def accept_suggestion(
    suggestion_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    suggestion = await _get_suggestion_with_auth(suggestion_id, principal, db, "editor")
    if suggestion.status != "pending":
        raise HTTPException(status_code=409, detail="Suggestion already reviewed.")

    scene = suggestion._scene
    entity = None
    if suggestion.matched_entity_id:
        entity = await db.get(models.Entity, suggestion.matched_entity_id)
    if entity is None:
        existing = await db.scalar(
            select(models.Entity).where(
                models.Entity.project_id == scene.screenplay.project_id,
                models.Entity.entity_type == suggestion.suggested_type,
                models.Entity.canonical_name.ilike(suggestion.suggested_name),
            )
        )
        if existing:
            entity = existing
        else:
            entity = models.Entity(
                project_id=scene.screenplay.project_id,
                entity_type=suggestion.suggested_type,
                canonical_name=suggestion.suggested_name,
            )
            db.add(entity)
            await db.flush()

    if suggestion.start_offset is not None and suggestion.end_offset is not None:
        db.add(
            models.Annotation(
                scene_id=suggestion.scene_id,
                node_id=suggestion.node_id,
                start_offset=suggestion.start_offset,
                end_offset=suggestion.end_offset,
                entity_id=entity.id,
                source="ai_accepted",
                created_by=principal.user_id,
            )
        )

    suggestion.status = "accepted"
    suggestion.matched_entity_id = entity.id
    suggestion.reviewed_by = principal.user_id
    suggestion.reviewed_at = datetime.now(UTC)
    await db.flush()

    from app.scenes import _rebuild_scene_entities
    await _rebuild_scene_entities(db, suggestion.scene_id)

    await db.commit()
    await db.refresh(suggestion)
    return _suggestion_out(suggestion)


@suggestions_router.post("/{suggestion_id}/reject", response_model=AiSuggestionOut)
async def reject_suggestion(
    suggestion_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    suggestion = await _get_suggestion_with_auth(suggestion_id, principal, db, "editor")
    if suggestion.status != "pending":
        raise HTTPException(status_code=409, detail="Suggestion already reviewed.")
    suggestion.status = "rejected"
    suggestion.reviewed_by = principal.user_id
    suggestion.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(suggestion)
    return _suggestion_out(suggestion)