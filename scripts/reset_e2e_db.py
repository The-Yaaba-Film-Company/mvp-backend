"""Idempotent schema reset for the real-backend E2E suite (mpcampo_e2e).

Recreates every table from the SQLAlchemy metadata (same path the pytest
db_engine fixture uses) so each Playwright run starts from a clean slate.
Run with DATABASE_URL exported and PYTHONPATH pointing at the repo root.
"""

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import models


async def main() -> None:
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())