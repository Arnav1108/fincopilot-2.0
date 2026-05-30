import pytest


@pytest.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Agent tests are pure unit tests — no database needed. Override parent fixture."""
    yield
