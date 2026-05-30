from __future__ import annotations

import asyncio
import json
import os
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
from app.services.sec_edgar import SUBMISSIONS_URL, edgar_user_agent, get_cik
from app.tasks.ingestion import ingest_document
from app.tools.base import BaseTool, ToolNotFoundError, ToolUpstreamError

logger = structlog.get_logger(__name__)

_SEC_FILING_TYPES: frozenset[str] = frozenset({"10-K", "10-Q"})
_POLL_TIMEOUT = 600.0
_HTTP_TIMEOUT = 30.0
_CONFIRM_TTL = 120
_CONFIRM_POLL_INTERVAL = 0.5

_DOC_TYPE_MAP: dict[str, DocumentType] = {
    "10-K": DocumentType.filing_10k,
    "10-Q": DocumentType.filing_10q,
    "transcript": DocumentType.transcript,
    "presentation": DocumentType.presentation,
}


def _redis_client() -> aioredis.Redis:
    return aioredis.from_url(settings.REDIS_URL, decode_responses=True)


class DocumentFinderTool(BaseTool[DocumentFinderInput, DocumentFinderOutput]):

    # ── Human-in-the-loop confirmation ────────────────────────────────────────

    async def _request_confirmation(
        self,
        ticker: str,
        filing_type: str,
        period: str,
        conversation_id: str,
    ) -> bool:
        """
        Emits a confirmation_required SSE event, then polls Redis for a yes/no
        response written by the chat endpoint when the user replies.
        Returns True to proceed, False to cancel (including timeout).
        """
        token = str(uuid.uuid4())
        redis_key = f"confirm:{token}"

        from app.agent.stream_context import emit_event  # lazy: avoids circular import at module load
        emit_event({
            "type": "confirmation_required",
            "token": token,
            "ticker": ticker,
            "filing_type": filing_type,
            "period": period,
            "description": f"{ticker} {filing_type} {period}".strip(),
        })
        logger.info(
            "confirmation_required_emitted",
            ticker=ticker,
            filing_type=filing_type,
            period=period,
            token=token,
        )

        redis_conn = _redis_client()
        try:
            # sentinel so the chat endpoint knows this token is valid
            await redis_conn.set(f"{redis_key}:pending", "1", ex=_CONFIRM_TTL)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + _CONFIRM_TTL

            while loop.time() < deadline:
                response = await redis_conn.get(redis_key)
                if response == "yes":
                    logger.info("confirmation_accepted", token=token)
                    return True
                if response == "no":
                    logger.info("confirmation_rejected", token=token)
                    return False
                await asyncio.sleep(_CONFIRM_POLL_INTERVAL)

            logger.warning("confirmation_timeout", token=token)
            return False
        finally:
            await redis_conn.aclose()

    async def __call__(self, input: DocumentFinderInput) -> DocumentFinderOutput:  # noqa: A002
        logger.debug("tool_called", tool_name="document_finder", ticker=input.ticker, filing_type=input.filing_type)

        resolved_ticker = input.ticker.upper()
        doc_type = _DOC_TYPE_MAP.get(input.filing_type, DocumentType.other)

        # Duplicate check before any external calls
        async with AsyncSessionFactory() as db:
            result = await db.execute(
                select(Document).where(
                    Document.ticker == resolved_ticker,
                    Document.doc_type == str(doc_type.value),
                    Document.status == DocumentStatus.ready,
                    Document.conversation_id == uuid.UUID(input.conversation_id),
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
        headers = {"User-Agent": edgar_user_agent(), "Accept-Encoding": "gzip, deflate"}

        # Phase 1: resolve filing metadata (no download yet)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            cik = await get_cik(ticker, client)

            subs_resp = await client.get(SUBMISSIONS_URL.format(cik=cik))
            subs_resp.raise_for_status()
            subs = subs_resp.json()

            filings = subs.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            accessions = filings.get("accessionNumber", [])
            report_dates = filings.get("reportDate", [])

            idx = next((i for i, f in enumerate(forms) if f == input.filing_type), None)
            if idx is None:
                raise ToolNotFoundError(f"No {input.filing_type} filing found for {ticker!r}")

            raw_acc = accessions[idx]
            acc_clean = raw_acc.replace("-", "")
            cik_int = int(cik)
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{raw_acc}.txt"
            )
            period = report_dates[idx] if idx < len(report_dates) else ""

        # Phase 2: ask the user before downloading the filing
        confirmed = await self._request_confirmation(
            ticker, input.filing_type, period, input.conversation_id
        )
        if not confirmed:
            return DocumentFinderOutput(status="cancelled", message="Download cancelled by user.")

        # Phase 3: download the filing
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            filing_resp = await client.get(filing_url)
            filing_resp.raise_for_status()
            raw_text = filing_resp.text

        # Extract only the 10-K section — the full .txt is 30-50MB with many embedded docs
        extracted = None
        for doc in raw_text.split("<DOCUMENT>"):
            if "<TYPE>10-K\n" in doc or "<TYPE>10-K\r" in doc:
                start = doc.find("<TEXT>")
                end = doc.find("</TEXT>")
                if start != -1 and end != -1:
                    extracted = doc[start + 6:end]
                    break

        if not extracted or len(extracted) < 1000:
            logger.warning("sec_10k_extraction_failed", ticker=ticker)
            extracted = raw_text  # fallback to full file

        content = extracted.encode("utf-8")
        file_ext = "html"

        import os as _os
        _os.makedirs("/app/uploads", exist_ok=True)
        import tempfile as _tempfile
        tmp_fd, tmp_path = _tempfile.mkstemp(suffix=f".{file_ext}", dir="/app/uploads")
        with _os.fdopen(tmp_fd, "wb") as tmp:
            tmp.write(content)

        return await self._ingest_and_wait(input, filing_url, tmp_path, file_ext, ticker)

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

        # Ask the user before downloading
        confirmed = await self._request_confirmation(
            ticker, input.filing_type, "", input.conversation_id
        )
        if not confirmed:
            return DocumentFinderOutput(status="cancelled", message="Download cancelled by user.")

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            resp = await http.get(source_url)
            resp.raise_for_status()
            content = resp.content

        uploads_dir = "/app/uploads"
        os.makedirs(uploads_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=f".{file_type}", dir=uploads_dir)
        with os.fdopen(tmp_fd, "wb") as tmp:
            tmp.write(content)

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
        try:
            user_uuid = uuid.UUID(input.user_id) if input.user_id else uuid.uuid4()
        except ValueError:
            user_uuid = uuid.uuid4()

        async with AsyncSessionFactory() as db:
            async with db.begin():
                doc = Document(
                    user_id=user_uuid,
                    conversation_id=uuid.UUID(input.conversation_id),
                    filename=f"{ticker}_{input.filing_type}.{file_type}",
                    source_url=source_url,
                    doc_type=str(doc_type.value),
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
