from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

I = TypeVar("I")
O = TypeVar("O")


class ToolError(Exception):
    pass


class ToolRateLimitError(ToolError):
    pass


class ToolValidationError(ToolError):
    pass


class ToolNotFoundError(ToolError):
    pass


class ToolConfigError(ToolError):
    pass


class ToolUpstreamError(ToolError):
    pass


class BaseTool(ABC, Generic[I, O]):
    @abstractmethod
    async def __call__(self, input: I) -> O:  # noqa: A002
        ...
