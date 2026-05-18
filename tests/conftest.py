import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.auth import clerk_auth
from app.database import Base, get_db
from app.main import app
from app.models.user import User

# Derive test DB URL by replacing the DB name — avoids touching production data
from app.config import settings
TEST_DATABASE_URL = settings.DATABASE_URL.replace("/fincopilot", "/fincopilot_test", 1)

_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_TestSession = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db():
    async with _TestSession() as session:
        yield session


@pytest.fixture
async def test_user(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    await db.delete(user)
    await db.commit()


@pytest.fixture
async def test_user_2(db: AsyncSession):
    user = User(clerk_user_id=f"test_{uuid.uuid4()}")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    await db.delete(user)
    await db.commit()


@pytest.fixture
async def client(test_user: User, db: AsyncSession):
    async def _auth():
        return test_user

    async def _db():
        yield db

    app.dependency_overrides[clerk_auth] = _auth
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def other_client(test_user_2: User, db: AsyncSession):
    async def _auth():
        return test_user_2

    async def _db():
        yield db

    app.dependency_overrides[clerk_auth] = _auth
    app.dependency_overrides[get_db] = _db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client():
    """Client with no auth override — hits the real clerk_auth dependency, triggering 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
