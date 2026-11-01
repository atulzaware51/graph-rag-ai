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
    monkeypatch.setattr(answer_service, "retrieve_chunks", lambda **_kwargs: [])
    result = answer_question(query="What were the sales?", dataset_id="dataset-1")

    assert result["status"] == "no_evidence"
    assert result["sources"] == []
    assert "couldn't find relevant information" in result["answer"]


def test_answer_question_requires_at_least_one_provider_key(monkeypatch):
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **_kwargs: sample_chunks(),
    )

    with pytest.raises(AnswerServiceError) as error:
        answer_question(query="What were sales?", dataset_id="dataset-1")

    assert error.value.code == "LLM_PROVIDER_NOT_CONFIGURED"


def test_answer_question_returns_grounded_answer_and_provider(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **_kwargs: sample_chunks(),
    )
    prompts = []

    def fake_generate_text(*, system_prompt="", user_prompt):
        prompts.append((system_prompt, user_prompt))
        return "January sales revenue was 50000 rupees.", "groq"

    monkeypatch.setattr(answer_service, "generate_text", fake_generate_text)
    result = answer_question(query="What were the sales?", dataset_id="dataset-1")

    assert result["status"] == "success"
    assert result["answer"] == "January sales revenue was 50000 rupees."
    assert result["llm_provider"] == "groq"
    assert result["sources"][0]["chunk_id"] == "chunk-1"
    assert "January sales revenue was 50000 rupees." in prompts[0][1]


def test_chat_answer_passes_selected_evidence_graph_and_history(monkeypatch):
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
    prompts = []
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **kwargs: (
            prompts.append(kwargs) or "Revenue is supported by the second sheet.",
            "gemini",
        ),
    )

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
        conversation_history=[{"role": "user", "content": "Earlier question"}],
    )

    prompt = prompts[0]["user_prompt"]
    assert retrieved_ids == selected_ids
    assert result["dataset_ids"] == selected_ids
    assert result["sources"][0]["dataset_id"] == "sheet-b"
    assert result["sources"][0]["graph_matches"][0]["sql_name"] == "revenue"
    assert "Earlier question" in prompt
    assert "private-sample-value" not in prompt
    assert "unretrieved-secret" not in prompt


def test_chat_answer_fails_if_mandatory_graph_retrieval_fails(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks_for_datasets",
        lambda **_kwargs: sample_chunks(),
    )

    with pytest.raises(AnswerServiceError) as error:
        answer_service.answer_question(
            query="Compare revenue",
            dataset_ids=["dataset-1"],
            graph_context={"nodes": [], "edges": []},
        )

    assert error.value.code == "GRAPH_RETRIEVAL_FAILED"


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
