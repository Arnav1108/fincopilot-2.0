import asyncio
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.pool import NullPool

from app.api.auth import clerk_auth
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.user import User

# asyncpg has known issues with Windows ProactorEventLoop; SelectorEventLoop works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Replace only the DB name (last path segment) to avoid hitting the username.
_base, _ = settings.DATABASE_URL.rsplit("/", 1)
TEST_DATABASE_URL = f"{_base}/fincopilot_test"

# NullPool: never reuse connections across tests — eliminates "Future attached to
# a different loop" errors that arise when asyncpg protocol Futures from the session
# loop are reused by function-scoped fixture setups in a different loop context.
_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
_TestSession = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# ── Session-level table setup ──────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def setup_test_db():
    async with _engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE TYPE message_role AS ENUM ('user', 'assistant')"))
        await conn.execute(text(
            "CREATE TYPE document_type AS ENUM "
            "('10-K', '10-Q', 'transcript', 'presentation', 'research_note', 'other')"
        ))
        await conn.execute(text(
            "CREATE TYPE document_status AS ENUM ('pending', 'processing', 'ready', 'failed')"
        ))
        await conn.execute(text(
            "CREATE TYPE investment_style_enum AS ENUM ('growth', 'value', 'blend')"
        ))
        await conn.execute(text(
            "CREATE TYPE output_format_enum AS ENUM ('concise', 'detailed', 'bullet_points')"
        ))
        await conn.run_sync(Base.metadata.create_all)
    yield
    await _engine.dispose()


# ── Per-test DB session (fixture setup / direct assertions only) ───────────

@pytest.fixture
async def db():
    async with _TestSession() as session:
        yield session


# ── User fixtures ──────────────────────────────────────────────────────────
# Use statement-level DELETE so SQLAlchemy doesn't try to null-out the FK in
# conversations (the ORM relationship has no passive_deletes). The DB-level
# ON DELETE CASCADE handles the child rows.

@pytest.fixture
async def test_user(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.commit()
    yield user
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()


@pytest.fixture
async def test_user_2(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.commit()
    yield user
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()


# ── HTTP client fixtures ───────────────────────────────────────────────────
# _db() yields a FRESH session per request so the route handler has its own
# connection — avoids concurrent asyncpg access from BaseHTTPMiddleware tasks.

@pytest.fixture
async def client(test_user: User):
    async def _auth():
        return test_user

    async def _db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[clerk_auth] = _auth
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def other_client(test_user_2: User):
    async def _auth():
        return test_user_2

    async def _db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[clerk_auth] = _auth
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client():
    """No auth override — clerk_auth runs for real, returning 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
