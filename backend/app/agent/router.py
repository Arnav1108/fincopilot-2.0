from __future__ import annotations

import openai
import structlog

from app.agent.state import AgentState
from app.agent.stream_context import emit_event
from app.config import settings

_SYSTEM_PROMPT = """\
You are a query router for a financial research assistant. Classify the user query into exactly one of three categories and respond with only that word.

Categories:
- simple: A straightforward lookup or question answerable from existing documents with a single retrieval step. No multi-step reasoning required.
- complex: A multi-step research question requiring multiple tools, comparisons, calculations, or synthesising information from several sources.
- ingest: The user wants to upload, process, or add a document to the knowledge base. Not a research question.

Examples:
Query: "What was Apple's revenue in Q3 2023?"
Category: simple

Query: "Compare the debt-to-equity ratios of Apple, Microsoft, and Google over the past three years and identify which has the strongest balance sheet."
Category: complex

Query: "Please ingest this 10-K filing for Tesla."
Category: ingest

Query: "Summarise the key risks mentioned in the uploaded document."
Category: simple

Query: "Build a DCF model for Amazon using the last four years of free cash flow and a 10% discount rate."
Category: complex

Query: "Add this earnings transcript to the system."
Category: ingest

Respond with only one word: simple, complex, or ingest.\
"""


async def router_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__).bind(
        conversation_id=state.get("conversation_id"),
    )
    emit_event({"type": "node_update", "node": "router_node", "status": "running"})
    try:
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        user_message = state["query"]
        context_parts: list[str] = []
        if state.get("conversation_summary"):
            context_parts.append(f"Conversation summary: {state['conversation_summary']}")
        if state.get("recent_messages"):
            for msg in state["recent_messages"]:
                context_parts.append(f"{msg['role']}: {msg['content']}")
        if context_parts:
            user_message = "\n".join(context_parts) + f"\n\nCurrent query: {state['query']}"

        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=10,
            temperature=0,
        )

        raw_output = response.choices[0].message.content or ""
        classification = raw_output.strip().lower()

        if classification not in {"simple", "complex", "ingest"}:
            logger.warning(
                "router_unknown_classification",
                raw_output=raw_output,
            )
            classification = "complex"

        logger.debug(
            "router_classified",
            classification=classification,
            query_length=len(state["query"]),
        )
        return {"classification": classification}

    except Exception as e:
        logger.error("router_error", error=str(e))
        return {"error": str(e)}
