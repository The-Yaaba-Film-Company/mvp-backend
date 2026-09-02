from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.config import Settings
from app.schema import ROLE_RANKS
from app.security import verify_csrf


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncSession:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


Db = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@dataclass
class Principal:
    user: models.User
    session: models.Session

    @property
    def user_id(self) -> UUID:
        return self.user.id


async def require_session(
    request: Request,
    db: Db,
    settings: SettingsDep,
) -> Principal:
    session_id = request.cookies.get(settings.session_cookie_name)
    if not session_id:
        raise HTTPException(status_code=401, detail="Session expired.")
    try:
        session = await db.get(models.Session, UUID(session_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Session expired.")
    if (
        session is None
        or session.revoked_at is not None
        or session.user is None
        or not session.user.is_active
        or session.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(status_code=401, detail="Session expired.")
    return Principal(user=session.user, session=session)


SessionPrincipal = Annotated[Principal, Depends(require_session)]


async def require_auth(
    request: Request,
    principal: SessionPrincipal,
    db: Db,
) -> Principal:
    if request.method in ("GET", "HEAD"):
        return principal
    token = request.headers.get("X-CSRF-Token", "")
    if not verify_csrf(principal.session.csrf_secret, principal.session.id, token):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")
    principal.session.last_seen_at = datetime.now(UTC)
    await db.flush()
    return principal


AuthPrincipal = Annotated[Principal, Depends(require_auth)]


def require_project_role(required_role: str = "viewer"):
    async def _check(
        project_id: UUID,
        principal: AuthPrincipal,
        db: Db,
    ) -> str:
        membership = (
            await db.execute(
                select(models.ProjectMember).where(
                    models.ProjectMember.project_id == project_id,
                    models.ProjectMember.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        if membership is not None:
            role = membership.role
        elif await db.scalar(
            select(models.Project.id).where(
                models.Project.id == project_id,
                models.Project.owner_id == principal.user_id,
            )
        ):
            role = "owner"
        else:
            raise HTTPException(status_code=403, detail="Not a project member.")
        if ROLE_RANKS[role] < ROLE_RANKS[required_role]:
            raise HTTPException(status_code=403, detail="Insufficient project role.")
        return role

    return _check