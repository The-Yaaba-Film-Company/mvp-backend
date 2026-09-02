from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select

from app import models
from app.deps import AuthPrincipal, Db, require_project_role
from app.schema import (
    AddMemberRequest,
    CreateProjectRequest,
    ProjectMemberOut,
    ProjectOut,
    UpdateProjectRequest,
)
from app.sentry import breadcrumb

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_out(project: models.Project, role: str) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        description=project.description,
        owner_id=project.owner_id,
        role=role,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    principal: AuthPrincipal,
    db: Db,
):
    rows = (
        await db.execute(
            select(models.Project, models.ProjectMember.role)
            .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
            .where(models.ProjectMember.user_id == principal.user_id)
            .order_by(models.Project.created_at.desc())
        )
    ).all()
    breadcrumb("db", "list projects", count=len(rows))
    return [_project_out(project, role) for project, role in rows]


@router.post("", status_code=201, response_model=ProjectOut)
async def create_project(
    payload: CreateProjectRequest,
    principal: AuthPrincipal,
    db: Db,
):
    project = models.Project(
        title=payload.title,
        description=payload.description,
        owner_id=principal.user_id,
    )
    db.add(project)
    await db.flush()
    db.add(
        models.ProjectMember(
            project_id=project.id,
            user_id=principal.user_id,
            role="owner",
        )
    )
    await db.commit()
    await db.refresh(project)
    return _project_out(project, "owner")


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID,
    role: Annotated[str, Depends(require_project_role("viewer"))],
    db: Db,
):
    project = await db.get(models.Project, project_id)
    return _project_out(project, role)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID,
    payload: UpdateProjectRequest,
    role: Annotated[str, Depends(require_project_role("editor"))],
    db: Db,
):
    project = await db.get(models.Project, project_id)
    patches = payload.model_dump(exclude_unset=True)
    for field, value in patches.items():
        setattr(project, field, value)
    await db.commit()
    await db.refresh(project)
    return _project_out(project, role)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("owner"))],
    db: Db,
):
    project = await db.get(models.Project, project_id)
    await db.delete(project)
    await db.commit()


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
async def list_members(
    project_id: UUID,
    _role: Annotated[str, Depends(require_project_role("viewer"))],
    db: Db,
):
    rows = (
        await db.execute(
            select(models.ProjectMember, models.User)
            .join(models.User, models.User.id == models.ProjectMember.user_id)
            .where(models.ProjectMember.project_id == project_id)
            .order_by(models.ProjectMember.added_at)
        )
    ).all()
    return [
        ProjectMemberOut(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=member.role,
            added_at=member.added_at,
        )
        for member, user in rows
    ]


@router.post("/{project_id}/members", status_code=201, response_model=ProjectMemberOut)
async def add_member(
    project_id: UUID,
    payload: AddMemberRequest,
    _role: Annotated[str, Depends(require_project_role("editor"))],
    db: Db,
):
    user = await db.get(models.User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    existing = await db.get(models.ProjectMember, (project_id, payload.user_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already a member.")
    member = models.ProjectMember(
        project_id=project_id, user_id=payload.user_id, role=payload.role
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return ProjectMemberOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        added_at=member.added_at,
    )


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: UUID,
    user_id: UUID,
    _role: Annotated[str, Depends(require_project_role("owner"))],
    db: Db,
):
    await db.execute(
        delete(models.ProjectMember).where(
            models.ProjectMember.project_id == project_id,
            models.ProjectMember.user_id == user_id,
        )
    )
    await db.commit()