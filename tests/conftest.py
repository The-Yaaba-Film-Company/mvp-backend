import os

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://mpcampo:mpcampo@127.0.0.1:55432/mpcampo_test",
)

TEST_DATABASE_URL = os.environ["DATABASE_URL"]

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import Settings
from app.main import create_app

REGISTER = {
    "email": "alice@example.com",
    "password": "password123",
    "display_name": "Alice",
}


@pytest.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE users, sessions, projects, project_members "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture
async def app(db_engine):
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        session_cookie_secure=False,
        nvidia_api_key="",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def make_client(app):
    def _make() -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        )

    return _make


async def register(client: AsyncClient, **overrides) -> dict:
    body = {**REGISTER, **overrides}
    resp = await client.post("/api/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    csrf = client.cookies["csrf_token"]
    return {"user": resp.json(), "csrf": csrf}


async def login(client: AsyncClient, email: str, password: str = "password123") -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"user": resp.json(), "csrf": client.cookies["csrf_token"]}


def csrf_headers(token: str) -> dict:
    return {"X-CSRF-Token": token}