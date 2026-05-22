from __future__ import annotations

import openai
import structlog

from app.agent.state import AgentState
from app.config import settings

_SYSTEM_PROMPT = """\
You are a financial research assistant. Your role is to synthesise information from retrieved document chunks into a clear, accurate answer for the analyst.

Rules:
1. Base your answer ONLY on the document chunks provided. Do not use outside knowledge.
2. Do NOT provide buy, sell, or hold recommendations. If asked, state clearly that your output does not constitute financial advice and describe only what the documents say.
3. Cite sources inline using [1], [2], etc., mapped to the chunk numbers provided.
4. Tailor your response to the analyst's profile: acknowledge their role and focus areas when relevant.
5. If the documents do not contain enough information to answer the query, say so explicitly.\
"""


async def synthesizer_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    try:
        reranked_chunks = state.get("reranked_chunks") or []
        if not reranked_chunks:
            return {"final_output": "No relevant documents were found to answer this query."}

        model = state.get("model") or "gpt-4o"

        # Build user message
        parts: list[str] = []

        if state.get("conversation_summary"):
            parts.append(f"Conversation summary:\n{state['conversation_summary']}")

        recent = (state.get("recent_messages") or [])[-3:]
        if recent:
            parts.append("Recent conversation:")
            for msg in recent:
                parts.append(f"{msg['role']}: {msg['content']}")

        analyst_profile = state.get("analyst_profile") or {}
        if analyst_profile:
            profile_lines = [f"{k}: {v}" for k, v in analyst_profile.items()]
            parts.append("Analyst profile:\n" + "\n".join(profile_lines))

        parts.append(f"Query: {state['query']}")

        chunk_lines: list[str] = []
        for i, chunk in enumerate(reranked_chunks[:10], start=1):
            chunk_lines.append(f"[{i}] {chunk['content']}")
        parts.append("Document chunks:\n" + "\n\n".join(chunk_lines))

        user_message = "\n\n".join(parts)

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            stream=True,
            temperature=0.2,
        )

        tokens: list[str] = []
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                tokens.append(delta)

        final_output = "".join(tokens)

        logger.debug(
            "synthesizer_completed",
            model=model,
            chunk_count=len(reranked_chunks),
            output_length=len(final_output),
        )
        return {"final_output": final_output}

    except Exception as e:
        logger.error("synthesizer_error", error=str(e))
        return {"error": str(e)}
