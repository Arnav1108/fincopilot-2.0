from __future__ import annotations

import structlog

from app.agent.state import AgentState
from app.agent.stream_context import emit_event
from app.config import settings
from app.services.openai_client import openai_client

_SYSTEM_PROMPT = """\
You are a query router for a financial research assistant. Classify the user query into exactly one of three categories and respond with only that word.

Categories:
- simple: Answerable with 0 or 1 tool call — a single data lookup, a single document retrieval, or a pure general-knowledge/definition question that needs no external data at all. No multiple data sources required.
- complex: Requires 2 or more tool calls — multi-ticker comparisons, calculations requiring data from multiple sources, or fetching a new document from the web or SEC EDGAR and then querying it.
- ingest: The user wants to upload or add a LOCAL file they have provided directly. Not a research question and not a web/SEC fetch.

IMPORTANT: When the context includes [has_documents: true], files are ALREADY ingested and ready to query. Questions about their content are "simple" or "complex" — NEVER "ingest". Only classify as "ingest" when the user is asking to ADD a new local file that is not yet in the system.

Examples:
Query: "What is Apple's current stock price?"
Category: simple

Query: "Explain what a P/E ratio is"
Category: simple

Query: "What does the uploaded document say about risks?"
Category: simple

Query: "What happened to Tesla stock this week?"
Category: simple

Query: "What was Apple's revenue in Q3 2023?"
Category: simple

Query: "Summarise the key risks mentioned in the uploaded document."
Category: simple

Query: "[has_documents: true]\nSummarise this document"
Category: simple

Query: "[has_documents: true]\nWhat does this file say about revenue?"
Category: simple

Query: "How is my portfolio doing?"
Category: simple

Query: "Which of my holdings is performing best?"
Category: simple

Query: "What is my total portfolio value?"
Category: simple

Query: "Compare Apple, Microsoft, and Google profit margins over the last 3 years"
Category: complex

Query: "Get Apple's 10-K and summarise the main risks"
Category: complex

Query: "Fetch Tesla's latest earnings transcript and tell me the key takeaways"
Category: complex

Query: "Build a DCF model for Amazon using the last four years of free cash flow and a 10% discount rate."
Category: complex

Query: "Compare the balance sheet strength of AAPL, MSFT, and GOOG"
Category: complex

Query: "Please ingest this 10-K filing for Tesla."
Category: ingest

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
        user_message = state["query"]
        context_parts: list[str] = []
        if state.get("has_uploaded_documents"):
            context_parts.append("[has_documents: true]")
        if state.get("conversation_summary"):
            context_parts.append(f"Conversation summary: {state['conversation_summary']}")
        if state.get("recent_messages"):
            for msg in state["recent_messages"]:
                context_parts.append(f"{msg['role']}: {msg['content']}")
        if context_parts:
            user_message = "\n".join(context_parts) + f"\n\nCurrent query: {state['query']}"

        response = await openai_client.chat.completions.create(
            model=settings.ROUTER_MODEL,
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
