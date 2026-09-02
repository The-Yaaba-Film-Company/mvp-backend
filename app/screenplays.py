from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.deps import AuthPrincipal, get_db, require_project_role
from app.schema import (
    CreateScreenplayRequest,
    ScreenplayOut,
    UpdateScreenplayRequest,
)
from app.sentry import breadcrumb

# Router for project-scoped screenplay operations
project_screenplays_router = APIRouter(prefix="/projects/{project_id}/screenplays", tags=["screenplays"])


def _screenplay_out(sp: models.Screenplay) -> ScreenplayOut:
    return ScreenplayOut(
        id=sp.id,
        project_id=sp.project_id,
        title=sp.title,
        locked_at=sp.locked_at,
        created_at=sp.created_at,
        updated_at=sp.updated_at,
    )


@project_screenplays_router.get("", response_model=list[ScreenplayOut])
async def list_screenplays(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.Screenplay)
        .where(models.Screenplay.project_id == project_id)
        .order_by(models.Screenplay.created_at.desc())
    )
    screenplays = result.scalars().all()
    breadcrumb("db", "list screenplays", project_id=str(project_id), count=len(screenplays))
    return [_screenplay_out(sp) for sp in screenplays]


@project_screenplays_router.post("", status_code=201, response_model=ScreenplayOut)
async def create_screenplay(
    project_id: UUID,
    payload: CreateScreenplayRequest,
    _role: Annotated[str, Depends(require_project_role("editor"))],
    db: AsyncSession = Depends(get_db),
):
    sp = models.Screenplay(project_id=project_id, title=payload.title.strip())
    db.add(sp)
    await db.commit()
    await db.refresh(sp)
    return _screenplay_out(sp)


# Router for direct screenplay operations (by ID)
screenplays_router = APIRouter(prefix="/screenplays", tags=["screenplays"])


@screenplays_router.get("/{screenplay_id}", response_model=ScreenplayOut)
async def get_screenplay(
    screenplay_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(models.Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    # Check project membership
    from app.deps import require_project_role
    await require_project_role("viewer")(sp.project_id, principal, db)
    return _screenplay_out(sp)


@screenplays_router.patch("/{screenplay_id}", response_model=ScreenplayOut)
async def update_screenplay(
    screenplay_id: UUID,
    payload: UpdateScreenplayRequest,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(models.Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    from app.deps import require_project_role
    await require_project_role("editor")(sp.project_id, principal, db)
    if sp.locked_at is not None:
        raise HTTPException(status_code=409, detail="Cannot modify locked screenplay.")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(sp, field, value)
    await db.commit()
    await db.refresh(sp)
    return _screenplay_out(sp)


@screenplays_router.post("/{screenplay_id}/lock", response_model=ScreenplayOut)
async def lock_screenplay(
    screenplay_id: UUID,
    principal: AuthPrincipal,
    db: AsyncSession = Depends(get_db),
):
    sp = await db.get(models.Screenplay, screenplay_id)
    if not sp:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    from app.deps import require_project_role
    await require_project_role("editor")(sp.project_id, principal, db)

    if sp.locked_at is None:
        from datetime import UTC, datetime
        sp.locked_at = datetime.now(UTC)

        result = await db.execute(
            select(models.Scene)
            .where(models.Scene.screenplay_id == screenplay_id)
            .order_by(models.Scene.order_key)
        )
        scenes = result.scalars().all()
        for i, scene in enumerate(scenes, 1):
            scene.number = str(i)
            scene.number_suffix = None
            scene.locked = True

    await db.commit()
    await db.refresh(sp)
    return _screenplay_out(sp)