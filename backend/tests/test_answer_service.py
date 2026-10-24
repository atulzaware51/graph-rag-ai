
"""Tests for the GraphSheet AI answer service."""

import pytest

from backend.app.services import answer_service
from backend.app.services.answer_service import (
    AnswerServiceError,
    answer_question,
)


def sample_chunks():
    return [
        {
            "chunk_id": "chunk-1",
            "dataset_id": "dataset-1",
            "chunk_index": 0,
            "text": "January sales revenue was 50000 rupees.",
            "metadata": {"sheet": "Sales"},
            "score": 2,
            "matched_terms": ["sales", "revenue"],
        }
    ]


def test_answer_question_returns_no_evidence(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **kwargs: [],
    )

    result = answer_question(
        query="What were the sales?",
        dataset_id="dataset-1",
    )

    assert result["status"] == "no_evidence"
    assert result["sources"] == []
    assert "couldn't find relevant information" in result["answer"]


def test_answer_question_requires_api_key(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **kwargs: sample_chunks(),
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(AnswerServiceError, match="GEMINI_API_KEY"):
        answer_question(
            query="What were the sales?",
            dataset_id="dataset-1",
        )


def test_answer_question_returns_gemini_answer(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")

    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **kwargs: sample_chunks(),
    )

    class FakeResponse:
        text = "January sales revenue was 50000 rupees."

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert "January sales revenue was 50000 rupees" in kwargs["contents"]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.models = FakeModels()

    monkeypatch.setattr(answer_service.genai, "Client", FakeClient)

    result = answer_question(
        query="What were the sales?",
        dataset_id="dataset-1",
    )

    assert result["status"] == "success"
    assert result["answer"] == "January sales revenue was 50000 rupees."
    assert result["sources"][0]["chunk_id"] == "chunk-1"


def test_answer_question_handles_gemini_failure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **kwargs: sample_chunks(),
    )

    class FakeClient:
        def __init__(self, api_key):
            self.models = self

        def generate_content(self, **kwargs):
            raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(answer_service.genai, "Client", FakeClient)

    with pytest.raises(AnswerServiceError, match="Gemini request failed"):
        answer_question(
            query="What were the sales?",
            dataset_id="dataset-1",
        )


@pytest.mark.parametrize(
    ("query", "dataset_id"),
    [
        ("", "dataset-1"),
        ("   ", "dataset-1"),
        ("sales", ""),
        ("sales", "   "),
    ],
)
def test_answer_question_rejects_invalid_input(query, dataset_id):
    with pytest.raises(ValueError):
        answer_question(query=query, dataset_id=dataset_id)
