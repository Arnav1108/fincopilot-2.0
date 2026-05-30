from __future__ import annotations

import json
from typing import Any

import structlog

from app.agent.state import AgentState, PlanStep
from app.agent.stream_context import emit_event
from app.config import settings
from app.services.openai_client import openai_client
from app.tools import TOOL_REGISTRY

_PLAN_STEP_KEYS = {"tool_name", "input"}

_SYSTEM_PROMPT = """\
You are a research planner for a financial analysis assistant. Decompose the user's query into an ordered list of independent tool calls.

Available tools:
- document_retrieval: Search the user's ingested documents using semantic search. Input: {"query": str}. Provide a focused search phrase (50-200 chars), not full instructions or file names.
- financial_data: Fetch live stock price, income statement, balance sheet, and cash flow for a single ticker. Input: {"ticker": str} (uppercase, e.g. "AAPL").
- web_search: Search the web for current news, market data, or analyst commentary. Input: {"query": str, "search_type": "news"|"general"|"financial", "max_results": int (1-10, default 5)}.
- company_comparator: Compare financial metrics side-by-side for multiple tickers. Input: {"tickers": ["AAPL", "MSFT"], "metrics": ["revenue", "pe_ratio", "debt_to_equity"]}.
- document_finder: Fetch and ingest an SEC filing or web document into this conversation. Input: {"ticker": str, "filing_type": "10-K"|"10-Q"|"transcript"|"presentation"|"other"}. Note: user_id and conversation_id are injected automatically.

IMPORTANT rules:
- All tools are independent data-fetchers. No tool depends on another tool's output. Do not reference other steps.
- Use the fewest steps needed to answer the query. Maximum 6 steps.
- Do NOT include financial_calculator — it does not exist.
- Do NOT use id, dependencies, or input_template fields — they do not exist in this format.
- When [has_documents: true] is in the context, include document_retrieval as the FIRST step in the plan.

Return a JSON object with a single key "steps" whose value is an ordered list of tool call objects.
Each object must have exactly two keys:
- "tool_name": one of the five tool names listed above
- "input": the structured input object for that tool

Example for "Compare Apple and Microsoft revenue and latest news":
{
  "steps": [
    {"tool_name": "company_comparator", "input": {"tickers": ["AAPL", "MSFT"], "metrics": ["revenue"]}},
    {"tool_name": "web_search", "input": {"query": "Apple Microsoft revenue comparison analyst views", "search_type": "news", "max_results": 5}}
  ]
}\
"""


def _extract_raw_steps(parsed: Any, logger: Any, raw_content: str) -> list[dict] | None:
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        # Preferred key — model should always use "steps"
        if "steps" in parsed and isinstance(parsed["steps"], list):
            return parsed["steps"]

        # Single step dict at top level
        if _PLAN_STEP_KEYS.issubset(parsed.keys()):
            return [parsed]

        # Other wrapped list (e.g. {"plan": [...]})
        list_values = [v for v in parsed.values() if isinstance(v, list)]
        if list_values:
            return list_values[0]

    logger.warning("planner_invalid_shape", raw=raw_content[:200])
    return None


async def planner_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    emit_event({"type": "node_update", "node": "planner_node", "status": "running"})
    try:
        if state["classification"] != "complex":
            return {"plan": []}

        has_docs = state.get("has_uploaded_documents", False)
        user_content = state["query"]
        if has_docs:
            user_content = f"[has_documents: true]\n{state['query']}"

        response = await openai_client.chat.completions.create(
            model=settings.PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed: Any = json.loads(raw_content)

        raw_steps = _extract_raw_steps(parsed, logger, raw_content)
        if raw_steps is None:
            return {"plan": []}

        valid_tool_names = set(TOOL_REGISTRY.keys())
        clean_steps: list[PlanStep] = []
        for step in raw_steps:
            if not isinstance(step, dict) or "tool_name" not in step:
                continue
            tool_name = step["tool_name"]
            if tool_name not in valid_tool_names:
                logger.warning("planner_invalid_tool", tool_name=tool_name)
                continue
            clean_steps.append(
                PlanStep(
                    tool_name=tool_name,
                    input=step.get("input") or {},
                )
            )

        if len(clean_steps) > 6:
            logger.warning("planner_plan_truncated", original_count=len(clean_steps))
            clean_steps = clean_steps[:6]

        logger.debug("planner_plan_created", step_count=len(clean_steps))
        return {"plan": clean_steps}

    except Exception as e:
        logger.error("planner_error", error=str(e))
        return {"error": str(e)}
