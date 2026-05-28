from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SECFilingInput(BaseModel):
    ticker: str
    filing_type: Literal["10-K", "10-Q"]
    confirmation_token: str | None = None
    conversation_id: str | None = None


class SECFilingPreviewOutput(BaseModel):
    preview: str
    confirmation_token: str
    company_name: str
    filed_date: str
    filing_url: str
    form_type: str


class SECFilingIngestOutput(BaseModel):
    document_id: str
    status: Literal["pending"]
