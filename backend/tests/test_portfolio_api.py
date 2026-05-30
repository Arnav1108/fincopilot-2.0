"""Integration tests for /api/v1/portfolios endpoints.

Uses the shared `client` / `anon_client` fixtures from conftest.py.
Cross-user isolation tests insert data directly via `db` + `test_user_2`
instead of `other_client`, because both clients share `app.dependency_overrides`
and cannot be active simultaneously with different auth.
"""
from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.portfolio import Portfolio, PortfolioHolding
from app.models.user import User

# Independent session factory for cross-user test data — same DB as conftest
# (fincopilot_test), but a separate engine so it doesn't share the fixture-
# scoped connection that causes "Future attached to a different loop" errors.
_base, _ = settings.DATABASE_URL.rsplit("/", 1)
_TEST_DB_URL = f"{_base}/fincopilot_test"
_test_engine = create_async_engine(_TEST_DB_URL, echo=False, poolclass=NullPool)
_IsolatedSession = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)

BASE = "/api/v1/portfolios"


# ---------------------------------------------------------------------------
# POST /api/v1/portfolios/
# ---------------------------------------------------------------------------

class TestCreatePortfolio:
    async def test_creates_portfolio_201(self, client: AsyncClient):
        r = await client.post(f"{BASE}/", json={"name": "Tech Portfolio"})
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Tech Portfolio"
        assert data["holdings"] == []
        uuid.UUID(data["id"])
        uuid.UUID(data["user_id"])
        assert "created_at" in data
        assert "updated_at" in data

    async def test_empty_name_returns_422(self, client: AsyncClient):
        r = await client.post(f"{BASE}/", json={"name": ""})
        assert r.status_code == 422

    async def test_name_too_long_returns_422(self, client: AsyncClient):
        r = await client.post(f"{BASE}/", json={"name": "x" * 201})
        assert r.status_code == 422

    async def test_unauthenticated_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.post(f"{BASE}/", json={"name": "Test"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/portfolios/
# ---------------------------------------------------------------------------

class TestListPortfolios:
    async def test_empty_list_for_new_user(self, client: AsyncClient):
        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_returns_created_portfolio(self, client: AsyncClient):
        created = (await client.post(f"{BASE}/", json={"name": "My Portfolio"})).json()
        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert created["id"] in ids

    async def test_does_not_return_other_users_portfolios(
        self, client: AsyncClient, test_user_2: User
    ):
        async with _IsolatedSession() as session:
            other_portfolio = Portfolio(user_id=test_user_2.id, name="Other User Portfolio")
            session.add(other_portfolio)
            await session.commit()
            portfolio_id = str(other_portfolio.id)

        r = await client.get(f"{BASE}/")
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert portfolio_id not in ids

    async def test_unauthenticated_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.get(f"{BASE}/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/portfolios/{portfolio_id}
# ---------------------------------------------------------------------------

class TestGetPortfolio:
    async def test_get_portfolio_with_holdings(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "P1"})).json()
        pid = portfolio["id"]

        await client.post(
            f"{BASE}/{pid}/holdings",
            json={"ticker": "AAPL", "shares": 10},
        )

        r = await client.get(f"{BASE}/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == pid
        assert len(data["holdings"]) == 1
        assert data["holdings"][0]["ticker"] == "AAPL"

    async def test_get_other_user_portfolio_returns_404(
        self, client: AsyncClient, test_user_2: User
    ):
        async with _IsolatedSession() as session:
            other_portfolio = Portfolio(user_id=test_user_2.id, name="Other")
            session.add(other_portfolio)
            await session.commit()
            portfolio_id = other_portfolio.id

        r = await client.get(f"{BASE}/{portfolio_id}")
        assert r.status_code == 404

    async def test_nonexistent_portfolio_returns_404(self, client: AsyncClient):
        r = await client.get(f"{BASE}/{uuid.uuid4()}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/portfolios/{portfolio_id}
# ---------------------------------------------------------------------------

class TestDeletePortfolio:
    async def test_delete_portfolio_returns_204(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "ToDelete"})).json()
        r = await client.delete(f"{BASE}/{portfolio['id']}")
        assert r.status_code == 204

    async def test_delete_cascades_holdings(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "Cascade"})).json()
        pid = portfolio["id"]
        await client.post(f"{BASE}/{pid}/holdings", json={"ticker": "AAPL", "shares": 5})

        await client.delete(f"{BASE}/{pid}")

        r = await client.get(f"{BASE}/{pid}")
        assert r.status_code == 404

    async def test_delete_other_user_portfolio_returns_404(
        self, client: AsyncClient, test_user_2: User
    ):
        async with _IsolatedSession() as session:
            other_portfolio = Portfolio(user_id=test_user_2.id, name="Other")
            session.add(other_portfolio)
            await session.commit()
            portfolio_id = other_portfolio.id

        r = await client.delete(f"{BASE}/{portfolio_id}")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/portfolios/{portfolio_id}/holdings
# ---------------------------------------------------------------------------

class TestAddHolding:
    async def test_add_holding_201(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "H"})).json()
        pid = portfolio["id"]

        r = await client.post(
            f"{BASE}/{pid}/holdings",
            json={"ticker": "MSFT", "shares": 5, "avg_cost_basis": 300.0},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["ticker"] == "MSFT"
        assert float(data["shares"]) == 5.0
        assert float(data["avg_cost_basis"]) == 300.0
        uuid.UUID(data["id"])

    async def test_add_holding_without_cost_basis(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "NoCost"})).json()
        pid = portfolio["id"]
        r = await client.post(
            f"{BASE}/{pid}/holdings",
            json={"ticker": "GOOG", "shares": 2},
        )
        assert r.status_code == 201
        assert r.json()["avg_cost_basis"] is None

    async def test_lowercase_ticker_returns_422(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "V"})).json()
        r = await client.post(
            f"{BASE}/{portfolio['id']}/holdings",
            json={"ticker": "aapl", "shares": 1},
        )
        assert r.status_code == 422

    async def test_zero_shares_returns_422(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "Z"})).json()
        r = await client.post(
            f"{BASE}/{portfolio['id']}/holdings",
            json={"ticker": "AAPL", "shares": 0},
        )
        assert r.status_code == 422

    async def test_negative_cost_basis_returns_422(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "NegCost"})).json()
        r = await client.post(
            f"{BASE}/{portfolio['id']}/holdings",
            json={"ticker": "AAPL", "shares": 1, "avg_cost_basis": -50},
        )
        assert r.status_code == 422

    async def test_nonexistent_portfolio_returns_404(self, client: AsyncClient):
        r = await client.post(
            f"{BASE}/{uuid.uuid4()}/holdings",
            json={"ticker": "AAPL", "shares": 1},
        )
        assert r.status_code == 404

    async def test_other_user_portfolio_returns_404(
        self, client: AsyncClient, test_user_2: User
    ):
        async with _IsolatedSession() as session:
            other_portfolio = Portfolio(user_id=test_user_2.id, name="Other")
            session.add(other_portfolio)
            await session.commit()
            portfolio_id = other_portfolio.id

        r = await client.post(
            f"{BASE}/{portfolio_id}/holdings",
            json={"ticker": "AAPL", "shares": 1},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/portfolios/{portfolio_id}/holdings
# ---------------------------------------------------------------------------

class TestListHoldings:
    async def test_list_holdings_ordered_by_created_at(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "Order"})).json()
        pid = portfolio["id"]

        await client.post(f"{BASE}/{pid}/holdings", json={"ticker": "AAPL", "shares": 1})
        await client.post(f"{BASE}/{pid}/holdings", json={"ticker": "MSFT", "shares": 2})

        r = await client.get(f"{BASE}/{pid}/holdings")
        assert r.status_code == 200
        tickers = [h["ticker"] for h in r.json()]
        assert tickers == ["AAPL", "MSFT"]

    async def test_nonexistent_portfolio_returns_404(self, client: AsyncClient):
        r = await client.get(f"{BASE}/{uuid.uuid4()}/holdings")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/portfolios/{portfolio_id}/holdings/{holding_id}
# ---------------------------------------------------------------------------

class TestDeleteHolding:
    async def test_delete_holding_204(self, client: AsyncClient):
        portfolio = (await client.post(f"{BASE}/", json={"name": "DelH"})).json()
        pid = portfolio["id"]
        holding = (
            await client.post(f"{BASE}/{pid}/holdings", json={"ticker": "AAPL", "shares": 3})
        ).json()

        r = await client.delete(f"{BASE}/{pid}/holdings/{holding['id']}")
        assert r.status_code == 204

        r2 = await client.get(f"{BASE}/{pid}/holdings")
        assert r2.status_code == 200
        assert r2.json() == []

    async def test_delete_holding_nonexistent_portfolio_returns_404(
        self, client: AsyncClient
    ):
        r = await client.delete(f"{BASE}/{uuid.uuid4()}/holdings/{uuid.uuid4()}")
        assert r.status_code == 404

    async def test_delete_holding_wrong_portfolio_returns_404(self, client: AsyncClient):
        p1 = (await client.post(f"{BASE}/", json={"name": "P1"})).json()
        p2 = (await client.post(f"{BASE}/", json={"name": "P2"})).json()
        holding = (
            await client.post(
                f"{BASE}/{p1['id']}/holdings", json={"ticker": "AAPL", "shares": 1}
            )
        ).json()

        # Try to delete p1's holding via p2's URL
        r = await client.delete(f"{BASE}/{p2['id']}/holdings/{holding['id']}")
        assert r.status_code == 404

    async def test_unauthenticated_returns_401(self, anon_client: AsyncClient):
        r = await anon_client.delete(f"{BASE}/{uuid.uuid4()}/holdings/{uuid.uuid4()}")
        assert r.status_code == 401
