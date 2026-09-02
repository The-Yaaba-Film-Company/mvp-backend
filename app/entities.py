from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import (
    CreateEntityRequest,
    EntityOut,
    MergeEntitiesRequest,
    UpdateEntityRequest,
)
from app.sentry import breadcrumb

# Router for project-scoped entity operations
project_entities_router = APIRouter(prefix="/projects/{project_id}/entities", tags=["entities"])


def _entity_out(entity: models.Entity) -> EntityOut:
    return EntityOut(
        id=entity.id,
        project_id=entity.project_id,
        entity_type=entity.entity_type.value,
        canonical_name=entity.canonical_name,
        aliases=entity.aliases,
        attributes=entity.attributes,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


@project_entities_router.get("", response_model=list[EntityOut])
async def list_entities(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    type: str = Query(...),
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # Convert lowercase string type to uppercase EntityType enum
    entity_type_enum = getattr(models.EntityType, type.lower())

    stmt = select(models.Entity).where(
        models.Entity.project_id == project_id,
        models.Entity.entity_type == entity_type_enum,
    )

    if q:
        q_lower = q.lower()
        stmt = stmt.where(
            models.Entity.canonical_name.ilike(f"%{q_lower}%")
        )

    stmt = stmt.order_by(models.Entity.canonical_name).limit(50)
    result = await db.execute(stmt)
    entities = result.scalars().all()
    breadcrumb("db", "list entities", project_id=str(project_id), type=entity_type_enum.value, count=len(entities))
    return [_entity_out(e) for e in entities]


@project_entities_router.post("", status_code=201, response_model=EntityOut)
async def create_entity(
    project_id: UUID,
    payload: CreateEntityRequest,
    _role: Annotated[str, Depends(require_project_role("editor"))],
    db: AsyncSession = Depends(get_db),
):
    # Convert string entity_type to EntityType enum
    entity_type_enum = getattr(models.EntityType, payload.entity_type.lower())

    existing = await db.scalar(
        select(models.Entity).where(
            models.Entity.project_id == project_id,
            models.Entity.entity_type == entity_type_enum,
            models.Entity.canonical_name.ilike(payload.canonical_name),
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="Entity already exists.")

    entity = models.Entity(
        project_id=project_id,
        entity_type=entity_type_enum,
        canonical_name=payload.canonical_name,
        aliases=payload.aliases,
        attributes=payload.attributes,
    )
    db.add(entity)
    await db.commit()
    await db.refresh(entity)
    return _entity_out(entity)


# Router for direct entity operations (by ID)
entities_router = APIRouter(prefix="/entities", tags=["entities"])


@entities_router.get("/{entity_id}", response_model=EntityOut)
async def get_entity(
    entity_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    entity = await db.get(models.Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found.")
    await require_project_role("viewer")(entity.project_id, principal, db)
    return _entity_out(entity)


@entities_router.patch("/{entity_id}", response_model=EntityOut)
async def update_entity(
    entity_id: UUID,
    payload: UpdateEntityRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    entity = await db.get(models.Entity, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found.")
    await require_project_role("editor")(entity.project_id, principal, db)

    data = payload.model_dump(exclude_unset=True)

    # Rename-everywhere (spec §48): propagate a canonical-name change through
    # every scene's content so display names stay consistent.
    if "canonical_name" in data and data["canonical_name"] != entity.canonical_name:
        old_name = entity.canonical_name
        new_name = data["canonical_name"]
        await _propagate_rename(db, entity, old_name, new_name)
        # Keep the old name discoverable via fuzzy search — unless the client
        # explicitly manages aliases in this request.
        if "aliases" not in data:
            entity.aliases = list(dict.fromkeys([*(entity.aliases or []), old_name]))

    for field, value in data.items():
        setattr(entity, field, value)
    await db.commit()
    await db.refresh(entity)
    return _entity_out(entity)


async def _propagate_rename(
    db: AsyncSession, entity: models.Entity, old_name: str, new_name: str
) -> None:
    import copy

    from sqlalchemy import select as sa_select

    from app.content_update import update_character_refs, update_location_heading

    result = await db.execute(
        sa_select(models.Scene)
        .join(models.Screenplay, models.Scene.screenplay_id == models.Screenplay.id)
        .where(models.Screenplay.project_id == entity.project_id)
    )
    for scene in result.scalars().all():
        if scene.content is None or not isinstance(scene.content, dict):
            continue
        # Deepcopy FIRST: mutating the loaded dict in place would also mutate
        # SQLAlchemy's committed-value snapshot, making the flush a no-op.
        content = copy.deepcopy(scene.content)
        if entity.entity_type == models.EntityType.character:
            changed = update_character_refs(content, str(entity.id), str(entity.id), new_name)
        elif entity.entity_type == models.EntityType.location and scene.location_entity_id == entity.id:
            changed = update_location_heading(content, old_name, new_name)
        else:
            changed = 0
        if changed:
            scene.content = content


@entities_router.post("/merge", response_model=EntityOut)
async def merge_entities(
    payload: MergeEntitiesRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    source = await db.get(models.Entity, payload.source_id)
    target = await db.get(models.Entity, payload.target_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Entity not found.")
    if source.project_id != target.project_id:
        raise HTTPException(status_code=400, detail="Entities must belong to the same project.")
    if source.entity_type != target.entity_type:
        raise HTTPException(status_code=400, detail="Entities must have the same type to be merged.")
    await require_project_role("editor")(source.project_id, principal, db)

    import copy
    from typing import cast

    from sqlalchemy import select as sa_select
    from sqlalchemy import update as sa_update

    from app.content_update import update_character_refs, update_location_heading
    from app.models import AiSuggestion, Annotation
    from app.scenes import _rebuild_scene_entities

    # Every scene that ever referenced the source must be rebuilt so the
    # derived scene_entities stay consistent with the merged content below.
    result = await db.execute(
        sa_select(Annotation.scene_id).where(Annotation.entity_id == source.id)
    )
    affected: set[UUID] = {sid for sid in result.scalars().all()}

    result = await db.execute(
        sa_select(models.Scene)
        .join(models.Screenplay, models.Scene.screenplay_id == models.Screenplay.id)
        .where(models.Screenplay.project_id == source.project_id)
    )
    for scene in result.scalars().all():
        if scene.content is None or not isinstance(scene.content, dict):
            continue
        # Deepcopy FIRST: mutating the loaded dict in place would also mutate
        # SQLAlchemy's committed-value snapshot, making the flush a no-op.
        content = copy.deepcopy(scene.content)
        changed = 0
        if source.entity_type == models.EntityType.character:
            changed = update_character_refs(
                content, str(source.id), str(target.id), target.canonical_name
            )
        elif source.entity_type == models.EntityType.location and scene.location_entity_id == source.id:
            changed = update_location_heading(content, source.canonical_name, target.canonical_name)
            scene.location_entity_id = target.id
        if changed:
            scene.content = content
            affected.add(cast(UUID, scene.id))

    # Move annotations + AI suggestion matches onto the survivor, then merge
    # the source's aliases so nothing silently becomes undiscoverable.
    await db.execute(
        sa_update(Annotation)
        .where(Annotation.entity_id == source.id)
        .values({Annotation.entity_id: target.id})
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        sa_update(AiSuggestion)
        .where(AiSuggestion.matched_entity_id == source.id)
        .values({AiSuggestion.matched_entity_id: target.id})
        .execution_options(synchronize_session=False)
    )
    merged_aliases = [*(target.aliases or []), *(source.aliases or []), source.canonical_name]
    target.aliases = list(dict.fromkeys(a for a in merged_aliases if a != target.canonical_name))

    for scene_id in affected:
        await _rebuild_scene_entities(db, scene_id)

    await db.delete(source)
    await db.commit()
    await db.refresh(target)
    return _entity_out(target)