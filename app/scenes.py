import re
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import models
from app.ai import auto_analyze
from app.content_order import extract_order, reconcile_order, sort_content_by_order
from app.content_validator import (
    ContentValidationError,
    compute_content_hash,
    extract_heading_info,
    validate_tiptap_content,
)
from app.deps import AuthPrincipal, get_db, require_project_role
from app.models import Annotation, Entity, EntityType, Scene, SceneEntity
from app.schema import (
    CreateSceneRequest,
    ReorderSceneRequest,
    ReorderSceneResponse,
    SceneListResponse,
    SceneOut,
    UpdateSceneRequest,
)
from app.sentry import breadcrumb

# Router for screenplay-scoped scene operations
screenplay_scenes_router = APIRouter(prefix="/screenplays/{screenplay_id}/scenes", tags=["scenes"])


def _base_number(number: str | None) -> str:
    """Strip the letter suffix: '2A' -> '2', '10B' -> '10'. Falls back to '1'."""
    if not number:
        return "1"
    match = re.match(r"\d+", number)
    return match.group(0) if match else "1"


def _next_suffix(taken: set[str]) -> str:
    """First unused letter suffix (spec §32: 1, 2, 2A, 3, 4)."""
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if letter not in (taken or set()):
            return letter
    return ""


async def _assign_post_lock_number(
    db: AsyncSession, screenplay_id: UUID, order_key: float, scene: "Scene"
) -> None:
    """Number a scene inserted into a locked screenplay (spec §32).

    Appended scenes take the next integer; mid-sequence inserts take the
    preceding scene's base number plus the next free letter suffix, without
    touching any existing scenes' numbers.
    """
    result = await db.execute(
        select(Scene)
        .where(Scene.screenplay_id == screenplay_id)
        .order_by(Scene.order_key)
    )
    existing = result.scalars().all()

    if not existing or order_key >= existing[-1].order_key:
        nums = []
        for s in existing:
            match = re.match(r"\d+", s.number or "")
            if match:
                nums.append(int(match.group(0)))
        scene.number = str(max(nums, default=0) + 1)
        scene.number_suffix = None
        return

    prev = next((s for s in reversed(existing) if s.order_key < order_key), None)
    base = _base_number(prev.number) if prev is not None else "1"
    taken = {s.number_suffix for s in existing if s.number == base}
    scene.number = base
    scene.number_suffix = _next_suffix(taken)


def _scene_out(scene: Scene) -> SceneOut:
    content = scene.content
    if scene.node_order:
        content = sort_content_by_order(content, scene.node_order)
    return SceneOut(
        id=scene.id,
        screenplay_id=scene.screenplay_id,
        order_key=scene.order_key,
        number=scene.number,
        number_suffix=scene.number_suffix,
        locked=scene.locked,
        int_ext=scene.int_ext.value if scene.int_ext else None,
        location_entity_id=scene.location_entity_id,
        time_of_day=scene.time_of_day,
        heading_modifier=scene.heading_modifier,
        content=content,
        content_hash=scene.content_hash,
        created_at=scene.created_at,
        updated_at=scene.updated_at,
    )


@screenplay_scenes_router.get("", response_model=SceneListResponse)
async def list_scenes(
    screenplay_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(models.Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    await require_project_role("viewer")(sp.project_id, principal, db)

    result = await db.execute(
        select(Scene)
        .where(Scene.screenplay_id == screenplay_id)
        .order_by(Scene.order_key)
    )
    scenes = result.scalars().all()
    breadcrumb("db", "list scenes", screenplay_id=str(screenplay_id), count=len(scenes))
    return SceneListResponse(items=[_scene_out(s) for s in scenes])


@screenplay_scenes_router.post("", status_code=201, response_model=SceneOut)
async def create_scene(
    screenplay_id: UUID,
    payload: CreateSceneRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(models.Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    await require_project_role("editor")(sp.project_id, principal, db)


    try:
        validate_tiptap_content(payload.content.model_dump())
    except ContentValidationError as e:
        raise HTTPException(status_code=422, detail=e.detail)

    max_order = await db.scalar(
        select(func.coalesce(func.max(Scene.order_key), 0.0)).where(
            Scene.screenplay_id == screenplay_id
        )
    )
    new_order = (max_order or 0.0) + 1.0

    heading = extract_heading_info(payload.content.model_dump())
    location_entity_id = None
    if heading["location"]:

        existing = await db.scalar(
            select(Entity).where(
                Entity.project_id == sp.project_id,
                Entity.entity_type == EntityType.location,
                Entity.canonical_name.ilike(heading["location"]),
            )
        )
        if existing:
            location_entity_id = existing.id
        else:
            new_loc = Entity(
                project_id=sp.project_id,
                entity_type=EntityType.location,
                canonical_name=heading["location"],
            )
            db.add(new_loc)
            await db.flush()
            location_entity_id = new_loc.id

    new_content = payload.content.model_dump()
    scene = Scene(
        screenplay_id=screenplay_id,
        order_key=new_order,
        content=new_content,
        content_hash=compute_content_hash(new_content),
        node_order=extract_order(new_content),
        int_ext=heading["int_ext"],
        location_entity_id=location_entity_id,
        time_of_day=heading["time_of_day"],
        heading_modifier=heading["heading_modifier"],
    )
    if sp.locked_at is not None:
        await _assign_post_lock_number(db, screenplay_id, new_order, scene)
    db.add(scene)
    await db.flush()  # Flush to get the scene ID

    # Rebuild scene_entities for the new scene
    await _rebuild_scene_entities(db, scene.id)

    settings = request.app.state.settings
    if settings.gemini_api_key:
        background_tasks.add_task(
            auto_analyze, settings, request.app.state.sessionmaker, scene.id
        )

    await db.commit()
    await db.refresh(scene)

    return _scene_out(scene)


# Router for direct scene operations (by ID)
scenes_router = APIRouter(prefix="/scenes", tags=["scenes"])


async def _get_scene_with_auth(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession,
    required_role: str = "viewer",
    *,
    allow_locked: bool = False,
) -> Scene:
    result = await db.execute(
        select(Scene)
        .options(selectinload(Scene.screenplay))
        .where(Scene.id == scene_id)
    )
    scene = result.scalar_one_or_none()
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found.")
    await require_project_role(required_role)(scene.screenplay.project_id, principal, db)
    if scene.screenplay.locked_at is not None and required_role == "editor" and not allow_locked:
        raise HTTPException(status_code=409, detail="Cannot modify scene in locked screenplay.")
    return scene


@scenes_router.get("/{scene_id}", response_model=SceneOut)
async def get_scene(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "viewer")
    return _scene_out(scene)


@scenes_router.patch("/{scene_id}", response_model=SceneOut)
async def update_scene(
    scene_id: UUID,
    payload: UpdateSceneRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "editor")

    data = payload.model_dump(exclude_unset=True)

    if "content" in data and data["content"] is not None:
        new_content = data["content"]
        if hasattr(new_content, "model_dump"):
            new_content = new_content.model_dump()
        try:
            validate_tiptap_content(new_content)
        except ContentValidationError as e:
            print("Content validation error:", e.detail)
            raise HTTPException(status_code=422, detail=e.detail)

        new_hash = compute_content_hash(new_content)
        if new_hash != scene.content_hash:
            incoming_order = extract_order(new_content)
            prev_order = scene.node_order or incoming_order
            canonical = reconcile_order(prev_order, incoming_order)
            if incoming_order != canonical:
                breadcrumb(
                    "content",
                    "node order repaired",
                    scene_id=str(scene_id),
                    repaired_count=sum(
                        1 for a, b in zip(incoming_order, canonical) if a != b
                    ),
                    order_length=len(canonical),
                )
            scene.node_order = canonical
            scene.content = new_content
            scene.content_hash = new_hash


            heading = extract_heading_info(new_content)
            location_entity_id = None
            if heading["location"]:
                existing = await db.scalar(
                    select(Entity).where(
                        Entity.project_id == scene.screenplay.project_id,
                        Entity.entity_type == EntityType.location,
                        Entity.canonical_name.ilike(heading["location"]),
                    )
                )

                if existing:
                    location_entity_id = existing.id
                else:
                    new_loc = Entity(
                        project_id=scene.screenplay.project_id,
                        entity_type=EntityType.location,
                        canonical_name=heading["location"],
                    )
                    db.add(new_loc)
                    await db.flush()
                    location_entity_id = new_loc.id

            scene.int_ext = heading["int_ext"]
            scene.location_entity_id = location_entity_id
            scene.time_of_day = heading["time_of_day"]
            scene.heading_modifier = heading["heading_modifier"]


            await _rebuild_scene_entities(db, scene.id)

    for field in ("int_ext", "location_entity_id", "time_of_day", "heading_modifier"):
        if field in data and data[field] is not None:
            setattr(scene, field, data[field])

    await db.commit()
    await db.refresh(scene)
    return _scene_out(scene)


async def _rebuild_scene_entities(db: AsyncSession, scene_id: UUID) -> None:
    scene = await db.execute(select(Scene).where(Scene.id == scene_id))
    scene = scene.scalar_one_or_none()
    print("scene:", scene)
    if not scene:
        return

    await db.execute(delete(SceneEntity).where(SceneEntity.scene_id == scene_id))

    scene = await db.get(Scene, scene_id)
    if not scene:
        return

    entity_counts: dict[UUID, int] = {}

    if scene.location_entity_id:
        entity_counts[scene.location_entity_id] = entity_counts.get(scene.location_entity_id, 0) + 1

    result = await db.execute(
        select(Annotation.entity_id, func.count(Annotation.id)).where(
            Annotation.scene_id == scene_id
        ).group_by(Annotation.entity_id)
    )
    for entity_id, count in result.all():
        entity_counts[entity_id] = entity_counts.get(entity_id, 0) + count

    char_ids = _extract_character_ids(scene.content)
    for char_id in char_ids:
        entity_counts[char_id] = entity_counts.get(char_id, 0) + 1

    for entity_id, count in entity_counts.items():
        db.add(SceneEntity(scene_id=scene_id, entity_id=entity_id, occurrence_count=count))


def _extract_character_ids(content: dict) -> list[UUID]:
    from uuid import UUID as UUIDType
    ids: list[UUID] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "character":
            attrs = node.get("attrs", {})
            char_id = attrs.get("characterId")
            if isinstance(char_id, str):
                try:
                    ids.append(UUIDType(char_id))
                except ValueError:
                    pass
        for child in node.get("content", []) or []:
            walk(child)

    walk(content)
    return ids


@scenes_router.delete("/{scene_id}", status_code=204)
async def delete_scene(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "editor")
    await db.delete(scene)
    await db.commit()


@scenes_router.post("/{scene_id}/reorder", response_model=ReorderSceneResponse)
async def reorder_scene(
    scene_id: UUID,
    payload: ReorderSceneRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "editor")

    scene.order_key = payload.order_key
    await db.commit()

    result = await db.execute(
        select(Scene)
        .where(Scene.screenplay_id == scene.screenplay_id)
        .order_by(Scene.order_key)
    )
    scenes = result.scalars().all()
    return ReorderSceneResponse(items=[_scene_out(s) for s in scenes])


@scenes_router.post("/{scene_id}/duplicate", status_code=201, response_model=SceneOut)
async def duplicate_scene(
    scene_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    scene = await _get_scene_with_auth(scene_id, principal, db, "editor", allow_locked=True)

    max_order = await db.scalar(
        select(func.coalesce(func.max(Scene.order_key), 0.0)).where(
            Scene.screenplay_id == scene.screenplay_id
        )
    )
    new_order = (max_order or 0.0) + 1.0

    new_scene = Scene(
        screenplay_id=scene.screenplay_id,
        order_key=new_order,
        content=scene.content,
        content_hash=scene.content_hash,
        node_order=scene.node_order,
        int_ext=scene.int_ext,
        location_entity_id=scene.location_entity_id,
        time_of_day=scene.time_of_day,
        heading_modifier=scene.heading_modifier,
    )
    if scene.screenplay.locked_at is not None:
        await _assign_post_lock_number(db, scene.screenplay_id, new_order, new_scene)
    db.add(new_scene)
    await db.commit()
    await db.refresh(new_scene)

    await _rebuild_scene_entities(db, new_scene.id)

    return _scene_out(new_scene)