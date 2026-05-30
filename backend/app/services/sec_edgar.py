from __future__ import annotations

import httpx
import structlog

from app.config import settings
from app.tools.base import ToolNotFoundError, ToolUpstreamError

logger = structlog.get_logger(__name__)

_CIK_CACHE: dict[str, str] = {}

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def edgar_user_agent() -> str:
    email = settings.SEC_EDGAR_CONTACT_EMAIL or "fincopilot@example.com"
    return f"FinCopilot Research Tool {email}"


async def get_cik(ticker: str, client: httpx.AsyncClient) -> str:
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]

    resp = await client.get(TICKERS_URL)
    resp.raise_for_status()
    data = resp.json()

    lookup = {
        v["ticker"].upper(): str(v["cik_str"]).zfill(10)
        for v in data.values()
    }
    _CIK_CACHE.update(lookup)

    if ticker not in _CIK_CACHE:
        raise ToolNotFoundError(f"Ticker {ticker!r} not found in SEC EDGAR")

    return _CIK_CACHE[ticker]


def find_primary_document(index_data: dict, cik: int, accession: str) -> str:
    documents = index_data.get("directory", {}).get("item", [])
    for doc in documents:
        name = doc.get("name", "")
        doc_type = doc.get("type", "")
        if doc_type in ("10-K", "10-Q") and name.endswith(".htm"):
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"

    for doc in documents:
        name = doc.get("name", "")
        if name.endswith(".htm") and not name.startswith("R"):
            return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"

    raise ToolUpstreamError(
        f"Could not find primary document in filing index for accession {accession}"
    )
