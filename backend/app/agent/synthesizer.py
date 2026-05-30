from __future__ import annotations

import json

import openai
import structlog

from app.agent.state import AgentState
from app.agent.stream_context import emit_event
from app.config import settings

_SYSTEM_PROMPT_RAG = """\
You are a financial research assistant. Synthesise information from the retrieved document chunks into a clear, accurate answer.

Rules:
1. Base your answer ONLY on the document chunks provided. Do not use outside knowledge.
2. Do NOT provide buy, sell, or hold recommendations. If asked, state clearly that your output does not constitute financial advice and describe only what the documents say.
3. Cite sources inline using [1], [2], etc., mapped to the chunk numbers provided.
4. Tailor your response to the analyst's profile: acknowledge their role and focus areas when relevant.
5. If the documents do not contain enough information to answer the query, say so explicitly.\
"""

_SYSTEM_PROMPT_TOOLS = """\
You are a financial research assistant. You have been provided with data fetched from financial tools. Use this data to answer the user's question.

Rules:
1. Base your answer on the tool data provided. Do all arithmetic, comparisons, and ratio calculations yourself from the numbers given.
2. If a tool failed, acknowledge it and work with whatever data is available.
3. Do NOT provide buy, sell, or hold recommendations.
4. Do not fabricate specific figures not present in the tool data.
5. If tool data is insufficient to answer, say so explicitly.
6. Be clear, precise, and formatted for a financial analyst.\
"""

_SYSTEM_PROMPT_LLM_ONLY = """\
You are a financial research assistant with broad general knowledge. Answer the user's question using your own knowledge.

Rules:
1. Answer clearly and concisely.
2. Do NOT provide buy, sell, or hold recommendations.
3. Do not fabricate specific figures or cite sources you don't have.
4. If the question is outside your knowledge, say so honestly.\
"""

# Keywords that signal the user expects an answer from uploaded documents.
# Queries containing any of these when no chunks exist get the "no documents" message
# rather than a general-knowledge LLM answer.
_DOCUMENT_KEYWORDS = frozenset([
    "document", "resume", "cv", "file", "upload", "attachment",
    "report", "filing", "transcript", "10-k", "10-q", "annual report",
    "the attached", "what does it say", "summarise the", "summarize the",
    "my resume", "my document", "my file", "my report",
])


def _is_document_specific(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _DOCUMENT_KEYWORDS)


async def synthesizer_node(state: AgentState) -> dict:
    logger = structlog.get_logger(__name__)
    emit_event({"type": "node_update", "node": "synthesizer_node", "status": "running"})
    try:
        tool_results: dict = state.get("tool_results") or {}
        reranked_chunks = state.get("reranked_chunks") or []

        successful = {k: v for k, v in tool_results.items() if v.get("status") == "ok"}
        failed = {k: v for k, v in tool_results.items() if v.get("status") == "error"}

        # No-docs guard: fire only when there is truly nothing to answer from
        if not reranked_chunks and not successful and _is_document_specific(state["query"]):
            msg = "No documents uploaded yet. Please attach a file to get started."
            emit_event({"type": "token", "token": msg})
            return {"final_output": msg}

        # System prompt selection
        if reranked_chunks:
            system_prompt = _SYSTEM_PROMPT_RAG
        elif successful:
            system_prompt = _SYSTEM_PROMPT_TOOLS
        else:
            system_prompt = _SYSTEM_PROMPT_LLM_ONLY

        model = state.get("model") or settings.SYNTHESIZER_MODEL

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

        # Tool results section
        if successful or failed:
            lines: list[str] = ["Tool Results:"]
            for envelope in successful.values():
                data_str = json.dumps(envelope["data"], separators=(",", ":"))
                if len(data_str) > 500:
                    data_str = data_str[:500] + "..."
                lines.append(f"{envelope['tool_name']} result: {data_str}")
            for envelope in failed.values():
                lines.append(f"{envelope['tool_name']} failed: {envelope['error']}")
            parts.append("\n".join(lines))

        # RAG chunks section
        if reranked_chunks:
            chunk_lines: list[str] = []
            for i, chunk in enumerate(reranked_chunks[:10], start=1):
                chunk_lines.append(f"[{i}] {chunk['content']}")
            parts.append("Document chunks:\n" + "\n\n".join(chunk_lines))

        user_message = "\n\n".join(parts)

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            stream=True,
            temperature=0.2,
        )

        tokens: list[str] = []
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                emit_event({"type": "token", "token": delta})
                tokens.append(delta)

        final_output = "".join(tokens)

        logger.debug(
            "synthesizer_completed",
            model=model,
            tool_result_count=len(tool_results),
            chunk_count=len(reranked_chunks),
            output_length=len(final_output),
        )
        return {"final_output": final_output}

    except Exception as e:
        logger.error("synthesizer_error", error=str(e))
        return {"error": str(e)}
