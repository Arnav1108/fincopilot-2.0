import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.user import User

BASE = "/api/v1/conversations"


# ── POST /api/v1/conversations/ ───────────────────────────────────────────

class TestCreateConversation:
    async def test_creates_and_returns_201(self, client: AsyncClient):
        r = await client.post(f"{BASE}/")
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "New Conversation"
        uuid.UUID(data["id"])  # valid UUID
        assert "created_at" in data
        assert "updated_at" in data

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.post(f"{BASE}/")
        assert r.status_code == 401


# ── GET /api/v1/conversations/ ────────────────────────────────────────────

class TestListConversations:
    async def test_empty_list_for_new_user(self, client: AsyncClient):
        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        assert r.json() == []

    async def test_returns_own_conversations(self, client: AsyncClient):
        r1 = (await client.post(f"{BASE}/")).json()
        r2 = (await client.post(f"{BASE}/")).json()

        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        ids = [c["id"] for c in r.json()]
        assert r1["id"] in ids
        assert r2["id"] in ids

    async def test_ordered_by_updated_at_desc(self, client: AsyncClient):
        r1 = (await client.post(f"{BASE}/")).json()
        r2 = (await client.post(f"{BASE}/")).json()
        # Rename r1 so its updated_at is the most recent
        await client.patch(f"{BASE}/{r1['id']}", json={"title": "Renamed"})

        ids = [c["id"] for c in (await client.get(f"{BASE}/")).json()]
        assert ids[0] == r1["id"]

    async def test_does_not_return_other_users_conversations(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        other_conv = Conversation(user_id=test_user_2.id, title="Other")
        db.add(other_conv)
        await db.commit()

        ids = [c["id"] for c in (await client.get(f"{BASE}/")).json()]
        assert str(other_conv.id) not in ids

    async def test_does_not_return_soft_deleted(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.delete(f"{BASE}/{conv_id}")

        ids = [c["id"] for c in (await client.get(f"{BASE}/")).json()]
        assert conv_id not in ids

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.get(f"{BASE}/")
        assert r.status_code == 401


# ── PATCH /api/v1/conversations/{id} ─────────────────────────────────────

class TestRenameConversation:
    async def test_returns_200_with_updated_title(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": "Renamed"})
        assert r.status_code == 200
        assert r.json()["title"] == "Renamed"

    async def test_updated_at_changes(self, client: AsyncClient):
        data = (await client.post(f"{BASE}/")).json()
        r = await client.patch(f"{BASE}/{data['id']}", json={"title": "Changed"})
        assert r.json()["updated_at"] != data["updated_at"]

    async def test_persists_on_refetch(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.patch(f"{BASE}/{conv_id}", json={"title": "Persisted"})

        titles = [c["title"] for c in (await client.get(f"{BASE}/")).json()]
        assert "Persisted" in titles

    async def test_strips_whitespace(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": "  Trimmed  "})
        assert r.status_code == 200
        assert r.json()["title"] == "Trimmed"

    async def test_returns_404_for_other_users_conversation(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        other_conv = Conversation(user_id=test_user_2.id, title="Not yours")
        db.add(other_conv)
        await db.commit()

        r = await client.patch(f"{BASE}/{other_conv.id}", json={"title": "Stolen"})
        assert r.status_code == 404

    async def test_returns_404_for_soft_deleted(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.delete(f"{BASE}/{conv_id}")
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": "Ghost"})
        assert r.status_code == 404

    async def test_returns_404_for_nonexistent_uuid(self, client: AsyncClient):
        r = await client.patch(f"{BASE}/{uuid.uuid4()}", json={"title": "Ghost"})
        assert r.status_code == 404

    async def test_returns_422_for_empty_string(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": ""})
        assert r.status_code == 422

    async def test_returns_422_for_whitespace_only(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": "   "})
        assert r.status_code == 422

    async def test_returns_422_for_title_over_255_chars(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={"title": "x" * 256})
        assert r.status_code == 422

    async def test_returns_422_for_missing_title_field(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.patch(f"{BASE}/{conv_id}", json={})
        assert r.status_code == 422

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.patch(f"{BASE}/{uuid.uuid4()}", json={"title": "x"})
        assert r.status_code == 401


# ── DELETE /api/v1/conversations/{id} ────────────────────────────────────

class TestDeleteConversation:
    async def test_returns_204(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.delete(f"{BASE}/{conv_id}")
        assert r.status_code == 204

    async def test_conversation_gone_from_list(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.delete(f"{BASE}/{conv_id}")

        ids = [c["id"] for c in (await client.get(f"{BASE}/")).json()]
        assert conv_id not in ids

    async def test_second_delete_returns_404(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.delete(f"{BASE}/{conv_id}")
        r = await client.delete(f"{BASE}/{conv_id}")
        assert r.status_code == 404

    async def test_returns_404_for_other_users_conversation(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        other_conv = Conversation(user_id=test_user_2.id, title="Not yours")
        db.add(other_conv)
        await db.commit()

        r = await client.delete(f"{BASE}/{other_conv.id}")
        assert r.status_code == 404

    async def test_returns_404_for_nonexistent_uuid(self, client: AsyncClient):
        r = await client.delete(f"{BASE}/{uuid.uuid4()}")
        assert r.status_code == 404

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.delete(f"{BASE}/{uuid.uuid4()}")
        assert r.status_code == 401


# ── GET /api/v1/conversations/{id}/messages ───────────────────────────────

class TestListMessages:
    async def test_returns_empty_list(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        r = await client.get(f"{BASE}/{conv_id}/messages")
        assert r.status_code == 200
        assert r.json() == []

    async def test_returns_404_for_other_users_conversation(
        self, client: AsyncClient, db: AsyncSession, test_user_2: User
    ):
        other_conv = Conversation(user_id=test_user_2.id, title="Not yours")
        db.add(other_conv)
        await db.commit()

        r = await client.get(f"{BASE}/{other_conv.id}/messages")
        assert r.status_code == 404

    async def test_returns_404_for_soft_deleted(self, client: AsyncClient):
        conv_id = (await client.post(f"{BASE}/")).json()["id"]
        await client.delete(f"{BASE}/{conv_id}")
        r = await client.get(f"{BASE}/{conv_id}/messages")
        assert r.status_code == 404

    async def test_returns_404_for_nonexistent_uuid(self, client: AsyncClient):
        r = await client.get(f"{BASE}/{uuid.uuid4()}/messages")
        assert r.status_code == 404

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.get(f"{BASE}/{uuid.uuid4()}/messages")
        assert r.status_code == 401
