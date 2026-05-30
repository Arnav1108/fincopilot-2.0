from __future__ import annotations

import json
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Union

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionFactory
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.user import User
from app.schemas.tools.sec_filing import SECFilingIngestOutput, SECFilingInput, SECFilingPreviewOutput
from app.services.sec_edgar import SUBMISSIONS_URL, edgar_user_agent, find_primary_document, get_cik
from app.tasks.ingestion import ingest_document
from app.tools.base import BaseTool, ToolConfigError, ToolNotFoundError, ToolUpstreamError, ToolValidationError
from app.tools.rate_limiter import edgar_rate_limiter

logger = structlog.get_logger(__name__)

_SYSTEM_USER_ID: uuid.UUID | None = None

_PREVIEW_CHARS = 2000
_PREVIEW_TTL = 3600
_CONFIRM_TTL = 600
_HTTP_TIMEOUT = 15.0


def _redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


async def _get_system_user_id() -> uuid.UUID:
    global _SYSTEM_USER_ID
    if _SYSTEM_USER_ID is not None:
        return _SYSTEM_USER_ID

    async with AsyncSessionFactory() as db:
        async with db.begin():
            result = await db.execute(
                select(User).where(User.clerk_user_id == "system").with_for_update()
            )
            user = result.scalar_one_or_none()
            if user is None:
                user = User(clerk_user_id="system")
                db.add(user)
                await db.flush()
                await db.refresh(user)

    _SYSTEM_USER_ID = user.id
    return _SYSTEM_USER_ID


class SECFilingFetchTool(BaseTool[SECFilingInput, Union[SECFilingPreviewOutput, SECFilingIngestOutput]]):
    async def __call__(  # noqa: A002
        self, input: SECFilingInput
    ) -> SECFilingPreviewOutput | SECFilingIngestOutput:
        if not settings.SEC_EDGAR_CONTACT_EMAIL:
            raise ToolConfigError("SEC_EDGAR_CONTACT_EMAIL is not configured")

        if input.confirmation_token:
            return await self._ingest(input)
        return await self._preview(input)

    async def _preview(self, input: SECFilingInput) -> SECFilingPreviewOutput:
        logger.debug("tool_called", tool_name="sec_filing_preview", ticker=input.ticker)
        redis = _redis_client()
        cache_key = f"edgar_preview:{input.ticker}:{input.filing_type}"

        cached = await redis.get(cache_key)
        if cached:
            logger.debug("sec_cache_hit", ticker=input.ticker, filing_type=input.filing_type)
            await redis.aclose()
            return SECFilingPreviewOutput(**json.loads(cached))

        logger.debug("sec_cache_miss", ticker=input.ticker, filing_type=input.filing_type)
        await edgar_rate_limiter.acquire()

        headers = {"User-Agent": edgar_user_agent(), "Accept-Encoding": "gzip, deflate"}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            cik = await get_cik(input.ticker, client)

            submissions_resp = await client.get(SUBMISSIONS_URL.format(cik=cik))
            submissions_resp.raise_for_status()
            submissions = submissions_resp.json()

            company_name = submissions.get("name", input.ticker)
            filings = submissions.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            accessions = filings.get("accessionNumber", [])
            filing_dates = filings.get("filingDate", [])

            filing_idx = next(
                (i for i, f in enumerate(forms) if f == input.filing_type), None
            )
            if filing_idx is None:
                raise ToolNotFoundError(
                    f"No {input.filing_type} filing found for {input.ticker}"
                )

            accession = accessions[filing_idx].replace("-", "")
            filed_date = filing_dates[filing_idx]
            index_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
                f"/{accession}/{accessions[filing_idx]}-index.json"
            )

            await edgar_rate_limiter.acquire()
            index_resp = await client.get(index_url)
            index_resp.raise_for_status()
            index_data = index_resp.json()

            filing_url = find_primary_document(index_data, int(cik), accession)

            await edgar_rate_limiter.acquire()
            doc_resp = await client.get(filing_url)
            doc_resp.raise_for_status()
            preview_text = doc_resp.text[:_PREVIEW_CHARS]

        token = uuid.uuid4().hex
        confirm_data = {
            "ticker": input.ticker,
            "filing_type": input.filing_type,
            "filing_url": filing_url,
            "company_name": company_name,
            "filed_date": filed_date,
        }
        await redis.setex(f"edgar_confirm:{token}", _CONFIRM_TTL, json.dumps(confirm_data))

        output = SECFilingPreviewOutput(
            preview=preview_text,
            confirmation_token=token,
            company_name=company_name,
            filed_date=filed_date,
            filing_url=filing_url,
            form_type=input.filing_type,
        )
        await redis.setex(cache_key, _PREVIEW_TTL, output.model_dump_json())
        await redis.aclose()

        logger.debug("tool_succeeded", tool_name="sec_filing_preview", ticker=input.ticker)
        return output

    async def _ingest(self, input: SECFilingInput) -> SECFilingIngestOutput:
        logger.debug("tool_called", tool_name="sec_filing_ingest", ticker=input.ticker)
        redis = _redis_client()
        confirm_key = f"edgar_confirm:{input.confirmation_token}"
        raw = await redis.get(confirm_key)
        await redis.aclose()

        if not raw:
            raise ToolValidationError("confirmation_token expired or invalid")

        confirm = json.loads(raw)
        filing_url = confirm["filing_url"]
        filed_date_str = confirm.get("filed_date")

        try:
            filing_date = date.fromisoformat(filed_date_str) if filed_date_str else None
        except ValueError:
            filing_date = None

        filing_type_map = {"10-K": DocumentType.filing_10k, "10-Q": DocumentType.filing_10q}
        doc_type = filing_type_map[confirm["filing_type"]]

        headers = {"User-Agent": edgar_user_agent(), "Accept-Encoding": "gzip, deflate"}
        tmp_path: str
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp_path = tmp.name
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
                async with client.stream("GET", filing_url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        tmp.write(chunk)

        system_user_id = await _get_system_user_id()

        async with AsyncSessionFactory() as db:
            async with db.begin():
                doc = Document(
                    user_id=system_user_id,
                    conversation_id=uuid.UUID(input.conversation_id) if input.conversation_id else None,
                    filename=f"{confirm['ticker']}_{confirm['filing_type']}.txt",
                    source_url=filing_url,
                    doc_type=str(doc_type.value),
                    ticker=confirm["ticker"],
                    filing_date=filing_date,
                    status=DocumentStatus.pending,
                )
                db.add(doc)
                await db.flush()
                await db.refresh(doc)
                doc_id = str(doc.id)

        ingest_document.delay(str(doc_id), tmp_path, "html", input.conversation_id)

        logger.debug("tool_succeeded", tool_name="sec_filing_ingest", document_id=doc_id)
        return SECFilingIngestOutput(document_id=doc_id, status="pending")


