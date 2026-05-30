"""Integration tests for GET /api/v1/memories and DELETE /api/v1/memories."""
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import UserMemory
from app.models.user import User


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _seed_memories(
    db: AsyncSession,
    user: User,
    count: int,
    conversation_id: uuid.UUID | None = None,
) -> list[UserMemory]:
    rows = [
        UserMemory(
            user_id=user.id,
            fact_type="ticker_interest",
            content=f"User watches ticker #{i}",
            conversation_id=conversation_id,
        )
        for i in range(count)
    ]
    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows


# ── GET /api/v1/memories ──────────────────────────────────────────────────────

async def test_get_memories_empty(client: AsyncClient):
    resp = await client.get("/api/v1/memories")
    assert resp.status_code == 200
    body = resp.json()
    assert body["memories"] == []
    assert body["count"] == 0


async def test_get_memories_returns_all(
    client: AsyncClient, db: AsyncSession, test_user: User
):
    await _seed_memories(db, test_user, count=3)

    resp = await client.get("/api/v1/memories")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert len(body["memories"]) == 3


async def test_get_memories_ordered_oldest_first(
    client: AsyncClient, db: AsyncSession, test_user: User
):
    await _seed_memories(db, test_user, count=3)

    resp = await client.get("/api/v1/memories")
    assert resp.status_code == 200
    timestamps = [m["created_at"] for m in resp.json()["memories"]]
    assert timestamps == sorted(timestamps)


async def test_get_memories_response_schema(
    client: AsyncClient, db: AsyncSession, test_user: User
):
    await _seed_memories(db, test_user, count=1)

    resp = await client.get("/api/v1/memories")
    memory = resp.json()["memories"][0]
    assert "id" in memory
    assert "fact_type" in memory
    assert "content" in memory
    assert "conversation_id" in memory
    assert "created_at" in memory


async def test_get_memories_unauthenticated(anon_client: AsyncClient):
    resp = await anon_client.get("/api/v1/memories")
    assert resp.status_code == 401


async def test_get_memories_user_isolation(
    client: AsyncClient,
    other_client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    test_user_2: User,
):
    await _seed_memories(db, test_user, count=2)
    await _seed_memories(db, test_user_2, count=3)

    resp1 = await client.get("/api/v1/memories")
    resp2 = await other_client.get("/api/v1/memories")

    assert resp1.json()["count"] == 2
    assert resp2.json()["count"] == 3


# ── DELETE /api/v1/memories ───────────────────────────────────────────────────

async def test_delete_memories_clears_all(
    client: AsyncClient, db: AsyncSession, test_user: User
):
    await _seed_memories(db, test_user, count=5)

    resp = await client.delete("/api/v1/memories")
    assert resp.status_code == 204

    check = await client.get("/api/v1/memories")
    assert check.json()["count"] == 0


async def test_delete_memories_idempotent(client: AsyncClient):
    # No memories exist — should still return 204
    resp = await client.delete("/api/v1/memories")
    assert resp.status_code == 204


async def test_delete_memories_unauthenticated(anon_client: AsyncClient):
    resp = await anon_client.delete("/api/v1/memories")
    assert resp.status_code == 401


async def test_delete_memories_does_not_affect_other_user(
    client: AsyncClient,
    other_client: AsyncClient,
    db: AsyncSession,
    test_user: User,
    test_user_2: User,
):
    await _seed_memories(db, test_user, count=2)
    await _seed_memories(db, test_user_2, count=3)

    # Delete user 1's memories
    resp = await client.delete("/api/v1/memories")
    assert resp.status_code == 204

    # User 2's memories should be untouched
    check = await other_client.get("/api/v1/memories")
    assert check.json()["count"] == 3
