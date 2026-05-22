from __future__ import annotations

import json
from typing import Any

import openai
import structlog

from app.agent.state import AgentState, PlanStep
from app.agent.stream_context import emit_event
from app.config import settings
from app.tools import TOOL_REGISTRY

_PLAN_STEP_KEYS = {"id", "tool_name", "input_template", "dependencies"}

_SYSTEM_PROMPT = """\
You are a research planner for a financial analysis assistant. Decompose the user's query into a directed acyclic graph (DAG) of tool calls.

Available tools:
- financial_calculator: Perform mathematical or financial calculations (DCF, ratios, growth rates). Input: expression or structured calculation description.
- financial_data: Fetch live or historical financial data for a ticker (price, revenue, earnings, ratios). Input: ticker symbol and metric name.
- news_fetch: Retrieve recent news articles for a company or topic. Input: company name or topic string.
- document_retrieval: Search the user's uploaded document knowledge base using semantic search. Input: natural language query.
- sec_filing: Fetch SEC EDGAR filings (10-K, 10-Q, 8-K) for a company. Input: ticker symbol and filing type.

Return a JSON object with a single key "steps" whose value is a list of plan step objects. Each step object must have exactly these four keys:
- "id": unique string identifier for this step (e.g. "step_1", "step_2")
- "tool_name": one of the five tool names listed above
- "input_template": string input for this tool; may reference {query} and {user_id}, and may reference a prior step's result using {<step_id>}
- "dependencies": list of step ids this step depends on; empty list if none

Rules:
- Keep the plan minimal: use the fewest steps needed to answer the query.
- Steps with no shared dependencies may run in parallel — give them empty dependency lists.
- A step may only depend on steps that appear earlier in the list.

Example output for "Compare Apple and Microsoft revenue":
{
  "steps": [
    {"id": "step_1", "tool_name": "financial_data", "input_template": "AAPL annual revenue for {query}", "dependencies": []},
    {"id": "step_2", "tool_name": "financial_data", "input_template": "MSFT annual revenue for {query}", "dependencies": []},
    {"id": "step_3", "tool_name": "financial_calculator", "input_template": "compare revenue: {step_1} vs {step_2}", "dependencies": ["step_1", "step_2"]}
  ]
}\
"""


def _extract_raw_steps(parsed: Any, logger: Any, raw_content: str) -> list[dict] | None:
    """Return a list of raw step dicts from the parsed JSON, or None if shape is unrecognisable."""
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        # Single PlanStep dict returned at the top level — wrap it
        if _PLAN_STEP_KEYS.issubset(parsed.keys()):
            return [parsed]

        # Model wrapped the list under a key (e.g. {"steps": [...]} or {"plan": [...]})
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

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": state["query"]},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )

        raw_content = response.choices[0].message.content or "{}"
        parsed: Any = json.loads(raw_content)

        raw_steps = _extract_raw_steps(parsed, logger, raw_content)
        if raw_steps is None:
            return {"plan": []}

        # Pass 1: drop steps with invalid or missing tool_name
        valid_tool_names = set(TOOL_REGISTRY.keys())
        tool_valid_steps: list[dict] = []
        for step in raw_steps:
            if not isinstance(step, dict) or "id" not in step or "tool_name" not in step:
                continue
            tool_name = step["tool_name"]
            step_id = step["id"]
            if tool_name not in valid_tool_names:
                logger.warning("planner_invalid_tool", tool_name=tool_name, step_id=step_id)
                continue
            tool_valid_steps.append(step)

        # Pass 2: drop steps whose dependencies reference an id not present after pass 1
        valid_ids = {step["id"] for step in tool_valid_steps}
        clean_steps: list[PlanStep] = []
        for step in tool_valid_steps:
            step_id = step["id"]
            dependencies: list[str] = step.get("dependencies") or []
            bad_deps = [d for d in dependencies if d not in valid_ids]
            if bad_deps:
                for bad in bad_deps:
                    logger.warning(
                        "planner_invalid_dependency",
                        step_id=step_id,
                        bad_dependency=bad,
                    )
                continue
            clean_steps.append(
                PlanStep(
                    id=step_id,
                    tool_name=step["tool_name"],
                    input_template=step.get("input_template") or "{query}",
                    dependencies=list(dependencies),
                )
            )

        logger.debug("planner_plan_created", step_count=len(clean_steps))
        return {"plan": clean_steps}

    except Exception as e:
        logger.error("planner_error", error=str(e))
        return {"error": str(e)}
