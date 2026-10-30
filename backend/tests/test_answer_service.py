
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


def test_chat_answer_passes_selected_evidence_graph_and_history(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    selected_ids = ["sheet-a", "sheet-b"]
    retrieved_ids = []

    def retrieve(**kwargs):
        retrieved_ids.extend(kwargs["dataset_ids"])
        assert "retrieval" in kwargs["graph_context"]
        return [
            {
                **sample_chunks()[0],
                "dataset_id": "sheet-b",
                "graph_matches": [
                    {
                        "column": "revenue",
                        "sql_name": "revenue",
                        "matched_terms": ["revenue"],
                    }
                ],
            }
        ]

    monkeypatch.setattr(answer_service, "retrieve_chunks_for_datasets", retrieve)

    class FakeResponse:
        text = "Revenue is supported by the second sheet."

    class FakeModels:
        def generate_content(self, **kwargs):
            prompt = kwargs["contents"]
            assert '"relationship":"candidate"' in prompt
            assert '"content":"Earlier question"' in prompt
            assert "Dataset ID: sheet-b" in prompt
            assert "private-sample-value" not in prompt
            assert "unretrieved-secret" not in prompt
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(answer_service.genai, "Client", FakeClient)

    result = answer_service.answer_question(
        query="Compare revenue",
        dataset_ids=selected_ids,
        graph_context={
            "relationship": "candidate",
            "nodes": [
                {
                    "id": "column:sheet-b:revenue",
                    "type": "Column",
                    "dataset_id": "sheet-b",
                    "label": "Revenue",
                    "sql_name": "revenue",
                    "sample_values": ["private-sample-value"],
                },
                {
                    "id": "evidence:sheet-b:other",
                    "type": "Evidence",
                    "dataset_id": "sheet-b",
                    "chunk_id": "other",
                    "chunk_index": 1,
                    "text": "unretrieved-secret",
                },
            ],
        },
        conversation_history=[
            {"role": "user", "content": "Earlier question"}
        ],
    )

    assert retrieved_ids == selected_ids
    assert result["dataset_ids"] == selected_ids
    assert result["sources"][0]["dataset_id"] == "sheet-b"
    assert result["sources"][0]["graph_matches"][0]["sql_name"] == "revenue"
    assert result["graph_retrieval"]["matched_columns"][0]["sql_name"] == (
        "revenue"
    )
    assert "sample_values" not in result["graph_retrieval"]["matched_columns"][0]


@pytest.mark.parametrize("dataset_ids", [[], ["sheet-a", ""], "sheet-a"])
def test_answer_question_rejects_invalid_dataset_id_lists(dataset_ids):
    with pytest.raises(ValueError, match="dataset"):
        answer_service.answer_question(
            query="What is the answer?",
            dataset_ids=dataset_ids,
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
