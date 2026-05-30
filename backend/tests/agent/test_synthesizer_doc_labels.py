from __future__ import annotations

from app.agent.synthesizer import _SYSTEM_PROMPT_RAG, _doc_label


# ---------------------------------------------------------------------------
# _doc_label helper — pure function tests, no mocks needed
# ---------------------------------------------------------------------------

def test_doc_label_uses_doc_filename():
    assert _doc_label({"doc_filename": "apple_q2_2024.pdf"}) == "apple_q2_2024.pdf"


def test_doc_label_truncates_to_60_chars():
    long_name = "x" * 70
    result = _doc_label({"doc_filename": long_name})
    assert len(result) == 60
    assert result == long_name[:60]


def test_doc_label_falls_back_to_document_id_prefix():
    meta = {"document_id": "a1b2c3d4-0000-0000-0000-000000000000"}
    assert _doc_label(meta) == "a1b2c3d4"


def test_doc_label_unknown_when_no_fields():
    assert _doc_label({}) == "unknown"


def test_doc_label_none_metadata():
    assert _doc_label(None) == "unknown"


def test_doc_label_prefers_doc_filename_over_document_id():
    meta = {
        "doc_filename": "apple_q2.pdf",
        "document_id": "a1b2c3d4-0000-0000-0000-000000000000",
    }
    assert _doc_label(meta) == "apple_q2.pdf"


# ---------------------------------------------------------------------------
# _SYSTEM_PROMPT_RAG — comparison rule is present
# ---------------------------------------------------------------------------

def test_system_prompt_rag_has_comparison_rule():
    assert "multiple distinct source documents" in _SYSTEM_PROMPT_RAG


def test_system_prompt_rag_has_source_label_instruction():
    assert "[Source:" in _SYSTEM_PROMPT_RAG
