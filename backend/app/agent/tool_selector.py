from __future__ import annotations

import json

import openai
import structlog
from pydantic import ValidationError

from app.agent.state import AgentState
from app.agent.stream_context import emit_event
from app.config import settings
from app.schemas.tools.company_comparator import CompanyComparatorInput
from app.schemas.tools.document_finder import DocumentFinderInput
from app.schemas.tools.document_retrieval import DocumentRetrievalInput
from app.schemas.tools.financial_data import FinancialDataInput
from app.schemas.tools.web_search import WebSearchInput
from app.tools import TOOL_REGISTRY

_TOOL_INPUT_MODELS = {
    "company_comparator": CompanyComparatorInput,
    "document_finder": DocumentFinderInput,
    "document_retrieval": DocumentRetrievalInput,
    "financial_data": FinancialDataInput,
    "web_search": WebSearchInput,
}

_SYSTEM_PROMPT = """\
You are a tool selector for a financial research assistant. Given the user's query, select the single most appropriate tool to answer it, or return null if no tool is needed.

Available tools:
- document_retrieval: Search through the user's ingested documents using semantic search. Input: {"query": str}. Use this when the user asks about content in their uploaded documents.
- financial_data: Fetch live stock price, income statement, balance sheet, and cash flow for a single ticker. Input: {"ticker": str} (uppercase, e.g. "AAPL"). Use for single-company financial lookups.
- web_search: Search the web for current news, market data, or analyst commentary. Input: {"query": str, "search_type": "news"|"general"|"financial", "max_results": int (1-10, default 5)}.
- company_comparator: Compare financial metrics side-by-side for multiple tickers. Input: {"tickers": ["AAPL", "MSFT"], "metrics": ["revenue", "pe_ratio"]}. Use when comparing metrics across 2+ companies (but only one category of metrics).
- document_finder: Fetch and ingest an SEC filing or web document into this conversation. Input: {"ticker": str, "filing_type": "10-K"|"10-Q"|"transcript"|"presentation"|"other"}. Note: user_id and conversation_id are injected automatically.

Rules:
- Select exactly ONE tool, or null if no tool is needed.
- Return null for: pure definitions ("what is a P/E ratio?"), conceptual explanations, general financial knowledge, or questions the model can answer from its own knowledge without live data.
- When the context includes [has_documents: true], PREFER document_retrieval for questions about the document content.
- Do NOT select financial_calculator — it does not exist.
- Do NOT select multiple tools — only one.
- Do NOT include user_id or conversation_id in the input object — they are injected automatically.

Return a JSON object with exactly these two keys:
{"tool_name": <one of the five tool names, or null>, "input": <structured input object, or {} if null>}

Examples:
Query: "What is Apple's current stock price?" → {"tool_name": "financial_data", "input": {"ticker": "AAPL"}}
Query: "Explain what a P/E ratio is" → {"tool_name": null, "input": {}}
Query: "What happened to Tesla this week?" → {"tool_name": "web_search", "input": {"query": "Tesla stock news this week", "search_type": "news", "max_results": 5}}
Query: "[has_documents: true]\\nWhat does the document say about risks?" → {"tool_name": "document_retrieval", "input": {"query": "risks"}}
Query: "Get Apple's latest 10-K" → {"tool_name": "document_finder", "input": {"ticker": "AAPL", "filing_type": "10-K"}}\
"""


async def tool_selector_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__).bind(
        conversation_id=state.get("conversation_id"),
    )
    emit_event({"type": "node_update", "node": "tool_selector_node", "status": "running"})
    try:
        if state["classification"] != "simple":
            return {"plan": []}

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        user_message = state["query"]
        if state.get("has_uploaded_documents"):
            user_message = f"[has_documents: true]\n{state['query']}"

        response = await client.chat.completions.create(
            model=settings.TOOL_SELECTOR_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=300,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed = json.loads(raw_content)

        tool_name = parsed.get("tool_name") or None
        tool_input: dict = parsed.get("input") or {}

        if not tool_name:
            logger.debug("tool_selector_no_tool", query_length=len(state["query"]))
            return {"plan": []}

        if tool_name not in TOOL_REGISTRY:
            logger.warning("tool_selector_invalid_tool", tool_name=tool_name)
            return {"plan": []}

        if tool_name in ("document_retrieval", "document_finder"):
            tool_input["user_id"] = state["user_id"]
            tool_input["conversation_id"] = state["conversation_id"]

        try:
            validated = _TOOL_INPUT_MODELS[tool_name].model_validate(tool_input)
        except ValidationError as e:
            logger.warning("tool_selector_input_invalid", tool_name=tool_name, error=str(e))
            return {"plan": []}

        logger.debug("tool_selector_selected", tool_name=tool_name)
        return {"plan": [{"tool_name": tool_name, "input": validated.model_dump(mode="json")}]}

    except Exception as e:
        logger.error("tool_selector_error", error=str(e))
        return {"error": str(e)}
