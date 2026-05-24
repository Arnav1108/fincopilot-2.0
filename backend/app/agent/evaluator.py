from __future__ import annotations

import openai
import structlog

from app.agent.state import AgentState
from app.agent.stream_context import emit_event
from app.config import settings

_SCORE_SYSTEM_PROMPT = """\
You are a retrieval quality evaluator for a financial research assistant.
Given a user query and a set of retrieved document chunks, rate how well the chunks answer the query.

Return a single floating-point number between 0.0 and 1.0:
- 1.0 = the chunks fully and directly answer the query
- 0.6 = the chunks partially answer the query with some relevant information
- 0.0 = the chunks are completely irrelevant to the query

Return ONLY the number. No explanation, no extra text.\
"""

_REFORMULATE_SYSTEM_PROMPT = """\
You are a query reformulation assistant for a financial research assistant.
The current retrieval query did not return high-quality results.
Rewrite the query to improve retrieval quality. Make it more specific, use financial terminology, and try alternative phrasings.

Return ONLY the reformulated query string. No explanation, no extra text.\
"""


async def evaluator_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    emit_event({"type": "node_update", "node": "evaluator_node", "status": "running"})
    try:
        reranked_chunks = state.get("reranked_chunks") or []
        if not reranked_chunks:
            # No chunks → nothing to score or reformulate; route straight to synthesizer.
            # Returning score=1.0 satisfies route_after_evaluator's ≥0.6 threshold so
            # the retry loop is skipped entirely. Synthesizer handles the no-docs case.
            return {
                "retrieval_quality_score": 1.0,
                "relevance_score": None,
                "retry_count": state["retry_count"],
                "query": state["query"],
            }

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        # Build scoring user message — up to 5 chunks, truncated to 500 chars each
        chunk_lines = []
        for i, chunk in enumerate(reranked_chunks[:5], start=1):
            text = chunk["content"][:500]
            chunk_lines.append(f"[{i}] {text}")
        chunks_text = "\n\n".join(chunk_lines)

        score_user_message = (
            f"Query: {state['query']}\n\n"
            f"Retrieved chunks:\n{chunks_text}"
        )

        score_response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SCORE_SYSTEM_PROMPT},
                {"role": "user", "content": score_user_message},
            ],
            max_tokens=10,
            temperature=0,
        )

        raw_score = (score_response.choices[0].message.content or "").strip()
        try:
            score = float(raw_score)
        except ValueError:
            logger.warning("evaluator_score_parse_warning", raw_response=raw_score)
            score = 0.0

        # Clamp to [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        will_retry = score < 0.6 and state["retry_count"] < 3

        logger.debug(
            "evaluator_scored",
            score=score,
            retry_count=state["retry_count"],
            will_retry=will_retry,
        )

        if not will_retry:
            return {
                "retrieval_quality_score": score,
                "relevance_score": score,
                "retry_count": state["retry_count"],
                "query": state["query"],
            }

        # Reformulate query for retry
        reformulate_response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _REFORMULATE_SYSTEM_PROMPT},
                {"role": "user", "content": state["query"]},
            ],
            max_tokens=150,
            temperature=0.3,
        )

        reformulated = (reformulate_response.choices[0].message.content or "").strip()
        if not reformulated:
            reformulated = state["query"]

        return {
            "retrieval_quality_score": score,
            "relevance_score": score,
            "retry_count": state["retry_count"] + 1,
            "query": reformulated,
        }

    except Exception as e:
        logger.error("evaluator_error", error=str(e))
        return {"error": str(e)}
