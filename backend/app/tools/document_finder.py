from __future__ import annotations

import asyncio
import json
import tempfile
import uuid

import httpx
import redis.asyncio as aioredis
import structlog
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionFactory
from app.models.document import Document, DocumentStatus, DocumentType
from app.schemas.tools.document_finder import DocumentFinderInput, DocumentFinderOutput
from app.tasks.ingestion import ingest_document
from app.tools.base import BaseTool, ToolNotFoundError, ToolUpstreamError

logger = structlog.get_logger(__name__)

_SEC_FILING_TYPES: frozenset[str] = frozenset({"10-K", "10-Q"})
_POLL_TIMEOUT = 120.0
_HTTP_TIMEOUT = 30.0
_CIK_CACHE: dict[str, str] = {}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_DOC_TYPE_MAP: dict[str, DocumentType] = {
    "10-K": DocumentType.filing_10k,
    "10-Q": DocumentType.filing_10q,
    "transcript": DocumentType.transcript,
    "presentation": DocumentType.presentation,
}


def _redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


def _user_agent() -> str:
    email = settings.SEC_EDGAR_CONTACT_EMAIL or "fincopilot@example.com"
    return f"FinCopilot Research Tool {email}"


async def _get_cik(ticker: str, client: httpx.AsyncClient) -> str:
    if ticker in _CIK_CACHE:
        return _CIK_CACHE[ticker]

    resp = await client.get(_TICKERS_URL)
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


class DocumentFinderTool(BaseTool[DocumentFinderInput, DocumentFinderOutput]):

    async def __call__(self, input: DocumentFinderInput) -> DocumentFinderOutput:  # noqa: A002
        logger.debug("tool_called", tool_name="document_finder", ticker=input.ticker, filing_type=input.filing_type)

        resolved_ticker = input.ticker.upper()
        doc_type = _DOC_TYPE_MAP.get(input.filing_type, DocumentType.other)

        # Duplicate check before any external calls
        async with AsyncSessionFactory() as db:
            result = await db.execute(
                select(Document).where(
                    Document.ticker == resolved_ticker,
                    Document.doc_type == doc_type,
                    Document.status == DocumentStatus.ready,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                logger.debug("document_finder_duplicate", ticker=resolved_ticker)
                return DocumentFinderOutput(
                    status="duplicate",
                    document_id=str(existing.id),
                    chunk_count=existing.chunk_count,
                )

        if input.filing_type in _SEC_FILING_TYPES:
            return await self._sec_route(input, resolved_ticker)
        return await self._web_route(input, resolved_ticker)

    # ── SEC EDGAR route ────────────────────────────────────────────────────────

    async def _sec_route(self, input: DocumentFinderInput, ticker: str) -> DocumentFinderOutput:
        headers = {"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"}

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            cik = await _get_cik(ticker, client)

            subs_resp = await client.get(_SUBMISSIONS_URL.format(cik=cik))
            subs_resp.raise_for_status()
            subs = subs_resp.json()

            filings = subs.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            accessions = filings.get("accessionNumber", [])

            idx = next((i for i, f in enumerate(forms) if f == input.filing_type), None)
            if idx is None:
                raise ToolNotFoundError(f"No {input.filing_type} filing found for {ticker!r}")

            raw_acc = accessions[idx]
            acc_clean = raw_acc.replace("-", "")
            cik_int = int(cik)
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{raw_acc}.txt"
            )

            filing_resp = await client.get(filing_url)
            filing_resp.raise_for_status()
            content = filing_resp.content

        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        return await self._ingest_and_wait(input, filing_url, tmp_path, "txt", ticker)

    # ── Web (Tavily) route ─────────────────────────────────────────────────────

    async def _web_route(self, input: DocumentFinderInput, ticker: str) -> DocumentFinderOutput:
        from tavily import TavilyClient

        tavily = TavilyClient(api_key=settings.TAVILY_API_KEY)
        query = f"{ticker} {input.filing_type}"
        response = tavily.search(query=query, max_results=5)
        results: list[dict] = (response.get("results", []) if isinstance(response, dict) else [])

        if not results:
            raise ToolNotFoundError(f"No {input.filing_type} document found for {ticker!r}")

        pdf_result = next(
            (r for r in results if r.get("url", "").lower().endswith(".pdf")),
            results[0],
        )
        source_url: str = pdf_result.get("url", "")
        file_type = "pdf" if source_url.lower().endswith(".pdf") else "html"

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.get(source_url)
            resp.raise_for_status()
            content = resp.content

        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        return await self._ingest_and_wait(input, source_url, tmp_path, file_type, ticker)

    # ── Shared ingest + pubsub wait ───────────────────────────────────────────

    async def _ingest_and_wait(
        self,
        input: DocumentFinderInput,
        source_url: str,
        tmp_path: str,
        file_type: str,
        ticker: str,
    ) -> DocumentFinderOutput:
        doc_type = _DOC_TYPE_MAP.get(input.filing_type, DocumentType.other)
        user_uuid = uuid.UUID(input.user_id) if input.user_id else uuid.uuid4()

        async with AsyncSessionFactory() as db:
            async with db.begin():
                doc = Document(
                    user_id=user_uuid,
                    conversation_id=uuid.UUID(input.conversation_id),
                    filename=f"{ticker}_{input.filing_type}.{file_type}",
                    source_url=source_url,
                    doc_type=doc_type,
                    ticker=ticker,
                    status=DocumentStatus.pending,
                )
                db.add(doc)
                await db.flush()
                await db.refresh(doc)
                doc_id = str(doc.id)

        ingest_document.delay(doc_id, tmp_path, file_type, input.conversation_id)

        redis_conn = _redis_client()
        pubsub = redis_conn.pubsub()
        channel = f"document.{doc_id}.status"
        await pubsub.subscribe(channel)

        try:
            async def _wait_for_status():
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        data = json.loads(message["data"])
                        if data.get("status") in ("ready", "failed"):
                            return data
                return {"status": "failed"}

            data = await asyncio.wait_for(_wait_for_status(), timeout=_POLL_TIMEOUT)
            return DocumentFinderOutput(
                status=data.get("status", "failed"),
                chunk_count=data.get("chunk_count"),
                document_id=doc_id,
                message=data.get("error_message"),
            )
        except asyncio.TimeoutError:
            return DocumentFinderOutput(
                status="failed",
                message="Ingestion timed out waiting for document processing",
                document_id=doc_id,
            )
        finally:
            await pubsub.unsubscribe(channel)
            await redis_conn.aclose()
