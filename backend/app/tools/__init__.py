from app.tools.base import (
    BaseTool,
    ToolConfigError,
    ToolError,
    ToolNotFoundError,
    ToolRateLimitError,
    ToolUpstreamError,
    ToolValidationError,
)
from app.tools.company_comparator import CompanyComparatorTool
from app.tools.document_finder import DocumentFinderTool
from app.tools.document_retrieval import DocumentRetrievalTool
from app.tools.financial_data import FinancialDataTool
from app.tools.web_search import WebSearchTool

TOOL_REGISTRY: dict[str, BaseTool] = {
    "company_comparator": CompanyComparatorTool(),
    "document_finder": DocumentFinderTool(),
    "financial_data": FinancialDataTool(),
    "document_retrieval": DocumentRetrievalTool(),
    "web_search": WebSearchTool(),
}

__all__ = [
    "TOOL_REGISTRY",
    "BaseTool",
    "ToolError",
    "ToolRateLimitError",
    "ToolValidationError",
    "ToolNotFoundError",
    "ToolConfigError",
    "ToolUpstreamError",
    "CompanyComparatorTool",
    "DocumentFinderTool",
    "FinancialDataTool",
    "DocumentRetrievalTool",
    "WebSearchTool",
]
