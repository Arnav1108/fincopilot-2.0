import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AnalystProfile, User
from app.schemas.profile import InvestmentStyle, ProfileRead, ProfileUpdate

BASE = "/api/v1/profile"


# ── Unit tests (no DB, no HTTP) ───────────────────────────────────────────

class TestProfileSchemas:
    def test_profile_update_valid_partial_body(self):
        body = ProfileUpdate(investment_style="growth")
        data = body.model_dump(exclude_unset=True)
        assert data == {"investment_style": InvestmentStyle.growth}

    def test_profile_update_invalid_investment_style_raises(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(investment_style="aggressive")

    def test_profile_update_custom_context_over_500_chars_raises(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(custom_context="x" * 501)

    def test_profile_update_lowercase_ticker_raises(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(tracked_tickers=["aapl"])

    def test_profile_read_null_sectors_coerced_to_empty_list(self):
        obj = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            sectors_of_interest=None,
            tracked_tickers=None,
            investment_style=None,
            preferred_output_format=None,
            custom_context=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        read = ProfileRead.model_validate(obj)
        assert read.sectors == []
        assert read.tracked_tickers == []


# ── GET /api/v1/profile/ ──────────────────────────────────────────────────

class TestGetProfile:
    async def test_returns_200_with_defaults_for_new_user(self, client: AsyncClient):
        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        data = r.json()
        assert data["sectors"] == []
        assert data["tracked_tickers"] == []
        assert data["investment_style"] is None
        assert data["preferred_output_format"] is None
        assert data["custom_context"] is None
        assert "id" in data
        assert "user_id" in data

    async def test_auto_create_is_idempotent(
        self, client: AsyncClient, db: AsyncSession, test_user: User
    ):
        await client.get(f"{BASE}/")
        await client.get(f"{BASE}/")
        result = await db.execute(
            select(func.count())
            .select_from(AnalystProfile)
            .where(AnalystProfile.user_id == test_user.id)
        )
        assert result.scalar() == 1

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.get(f"{BASE}/")
        assert r.status_code == 401


# ── PUT /api/v1/profile/ ──────────────────────────────────────────────────

class TestPutProfile:
    async def test_put_then_get_reflects_values(self, client: AsyncClient):
        r = await client.put(
            f"{BASE}/", json={"investment_style": "growth", "sectors": ["Technology"]}
        )
        assert r.status_code == 200
        data = r.json()
        assert data["investment_style"] == "growth"
        assert "Technology" in data["sectors"]

        r2 = await client.get(f"{BASE}/")
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["investment_style"] == "growth"
        assert "Technology" in data2["sectors"]

    async def test_partial_update_does_not_overwrite_other_fields(
        self, client: AsyncClient
    ):
        await client.put(
            f"{BASE}/", json={"investment_style": "growth", "sectors": ["Energy"]}
        )
        r = await client.put(f"{BASE}/", json={"custom_context": "Focus on dividends."})
        assert r.status_code == 200
        data = r.json()
        assert data["investment_style"] == "growth"
        assert "Energy" in data["sectors"]
        assert data["custom_context"] == "Focus on dividends."

    async def test_clears_array_with_empty_list(self, client: AsyncClient):
        await client.put(f"{BASE}/", json={"sectors": ["Technology"]})
        r = await client.put(f"{BASE}/", json={"sectors": []})
        assert r.status_code == 200
        assert r.json()["sectors"] == []

        r2 = await client.get(f"{BASE}/")
        assert r2.json()["sectors"] == []

    async def test_invalid_investment_style_returns_422(self, client: AsyncClient):
        r = await client.put(f"{BASE}/", json={"investment_style": "aggressive"})
        assert r.status_code == 422

    async def test_custom_context_over_500_chars_returns_422(self, client: AsyncClient):
        r = await client.put(f"{BASE}/", json={"custom_context": "x" * 501})
        assert r.status_code == 422

    async def test_lowercase_ticker_returns_422(self, client: AsyncClient):
        r = await client.put(f"{BASE}/", json={"tracked_tickers": ["aapl"]})
        assert r.status_code == 422

    async def test_no_token_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.put(f"{BASE}/", json={"investment_style": "blend"})
        assert r.status_code == 401
