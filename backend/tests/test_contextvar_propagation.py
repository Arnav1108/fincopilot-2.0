"""
Empirical propagation test: verify that a ContextVar value set before
asyncio.create_task is visible inside LangGraph node coroutines.

This test MUST pass before stream_context.py is written.
If it fails, the production design must switch to a module-level
WeakValueDictionary keyed by run_id instead of a ContextVar.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TypedDict

from langgraph.graph import END, StateGraph


# ---------------------------------------------------------------------------
# Minimal probe setup — ContextVar + single-node graph
# ---------------------------------------------------------------------------

_cv: ContextVar[asyncio.Queue | None] = ContextVar("_cv", default=None)


class _State(TypedDict):
    pass


async def _probe_node(state: _State) -> dict:
    q = _cv.get()
    if q is not None:
        q.put_nowait("seen")
    return {}


_builder = StateGraph(_State)
_builder.add_node("probe", _probe_node)
_builder.set_entry_point("probe")
_builder.add_edge("probe", END)
_graph = _builder.compile()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

async def test_contextvar_visible_in_langgraph_node() -> None:
    """
    ContextVar set before create_task must be readable inside the LangGraph node.

    Pass  → ContextVar approach is safe; proceed with stream_context.py.
    Fail  → ContextVar does not propagate through LangGraph internals;
             switch every subsequent step to WeakValueDictionary fallback.
    """
    q: asyncio.Queue[str] = asyncio.Queue()
    token = _cv.set(q)
    try:
        task = asyncio.create_task(_graph.ainvoke({}))
        await task

        if q.empty():
            raise AssertionError(
                "ContextVar was NOT visible inside the LangGraph node — queue is empty. "
                "Switch to WeakValueDictionary fallback before proceeding."
            )

        result = q.get_nowait()
        assert result == "seen", (
            f"Unexpected queue value: expected 'seen', got {result!r}. "
            "Investigate LangGraph context propagation."
        )
    finally:
        _cv.reset(token)
