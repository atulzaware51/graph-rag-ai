"""Tests for GraphSheet AI keyword-based retrieval."""

import pytest

from backend.app.services.retrieval import (
    RetrievalError,
    retrieve_chunks,
    tokenize,
)


def fake_chunks():
    return [
        {
            "chunk_id": "chunk-1",
            "dataset_id": "dataset-1",
            "chunk_index": 0,
            "text": "Sales revenue for January was 50000",
            "metadata": {"sheet": "Sales"},
        },
        {
            "chunk_id": "chunk-2",
            "dataset_id": "dataset-1",
            "chunk_index": 1,
            "text": "Marketing expenses for January were 12000",
            "metadata": {"sheet": "Expenses"},
        },
        {
            "chunk_id": "chunk-3",
            "dataset_id": "dataset-1",
            "chunk_index": 2,
            "text": "Sales revenue for February was 75000",
            "metadata": {"sheet": "Sales"},
        },
    ]


def test_tokenize_lowercases_and_removes_stop_words():
    assert tokenize("Show Sales Revenue for January") == [
        "sales",
        "revenue",
        "january",
    ]


def test_tokenize_rejects_non_string():
    with pytest.raises(RetrievalError):
        tokenize(123)


def test_retrieve_chunks_returns_matching_results(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: fake_chunks(),
    )

    results = retrieve_chunks(
        query="sales revenue",
        dataset_id="dataset-1",
    )

    assert len(results) == 2
    assert results[0]["chunk_id"] == "chunk-1"
    assert results[1]["chunk_id"] == "chunk-3"
    assert all(result["score"] > 0 for result in results)


def test_retrieve_chunks_ranks_more_matches_first(monkeypatch):
    chunks = [
        {
            "chunk_id": "chunk-a",
            "dataset_id": "dataset-1",
            "chunk_index": 0,
            "text": "Sales report",
            "metadata": {},
        },
        {
            "chunk_id": "chunk-b",
            "dataset_id": "dataset-1",
            "chunk_index": 1,
            "text": "Sales revenue revenue report",
            "metadata": {},
        },
    ]

    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: chunks,
    )

    results = retrieve_chunks("sales revenue", "dataset-1")

    assert results[0]["chunk_id"] == "chunk-b"
    assert results[0]["score"] > results[1]["score"]


def test_retrieve_chunks_respects_top_k(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: fake_chunks(),
    )

    results = retrieve_chunks(
        query="sales revenue",
        dataset_id="dataset-1",
        top_k=1,
    )

    assert len(results) == 1


def test_retrieve_chunks_returns_empty_for_no_matches(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: fake_chunks(),
    )

    results = retrieve_chunks(
        query="quantum physics",
        dataset_id="dataset-1",
    )

    assert results == []


@pytest.mark.parametrize(
    ("query", "dataset_id", "top_k"),
    [
        ("", "dataset-1", 5),
        ("sales", "", 5),
        ("sales", "dataset-1", 0),
        ("sales", "dataset-1", -1),
        ("sales", "dataset-1", True),
    ],
)
def test_retrieve_chunks_rejects_invalid_arguments(
    query, dataset_id, top_k
):
    with pytest.raises(RetrievalError):
        retrieve_chunks(
            query=query,
            dataset_id=dataset_id,
            top_k=top_k,
        )


def test_retrieve_chunks_omits_internal_sort_field(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: fake_chunks(),
    )

    results = retrieve_chunks("sales", "dataset-1")

    assert results
    assert all("_original_position" not in result for result in results)
