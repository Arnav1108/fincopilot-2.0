from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token

_event_queue: ContextVar[asyncio.Queue | None] = ContextVar("_event_queue", default=None)


def set_stream_queue(queue: asyncio.Queue) -> Token:
    return _event_queue.set(queue)


def emit_event(event: dict) -> None:
    q = _event_queue.get()
    if q is not None:
        q.put_nowait(event)


def reset_stream_queue(token: Token) -> None:
    _event_queue.reset(token)
