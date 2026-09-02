from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import func, select

from app import models
from app.config import Settings
from app.deps import AuthPrincipal, Db, SessionPrincipal, SettingsDep
from app.schema import LoginRequest, RegisterRequest, UserOut
from app.security import (
    csrf_token_value,
    hash_password,
    new_secret,
    session_expiry,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, session: models.Session, settings: Settings) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        str(session.id),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token_value(session.csrf_secret, session.id),
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


async def _create_session(
    db: Db, user: models.User, settings: Settings
) -> models.Session:
    session = models.Session(
        user_id=user.id,
        csrf_secret=new_secret(),
        expires_at=session_expiry(settings.session_idle_minutes),
    )
    db.add(session)
    await db.flush()
    return session


@router.post("/register", status_code=201, response_model=UserOut)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: Db,
    settings: SettingsDep,
):
    email = str(payload.email).lower()
    existing = await db.scalar(select(models.User).where(models.User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered.")
    user = models.User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    await db.flush()
    session = await _create_session(db, user, settings)
    await db.commit()
    _set_auth_cookies(response, session, settings)
    return user


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest,
    response: Response,
    db: Db,
    settings: SettingsDep,
):
    email = str(payload.email).lower()
    user = await db.scalar(select(models.User).where(models.User.email == email))
    if user is None or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    session = await _create_session(db, user, settings)
    await db.commit()
    _set_auth_cookies(response, session, settings)
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    principal: AuthPrincipal,
    db: Db,
    settings: SettingsDep,
):
    principal.session.revoked_at = func.now()
    await db.commit()
    _clear_auth_cookies(response, settings)


@router.get("/me", response_model=UserOut)
async def me(principal: SessionPrincipal):
    return principal.user