"""Tests for GraphSheet AI keyword-based retrieval."""

import pytest

from backend.app.services.retrieval import (
    RetrievalError,
    build_graph_retrieval_context,
    retrieve_chunks,
    retrieve_chunks_for_datasets,
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
        "sale",
        "revenue",
        "january",
    ]


def test_tokenize_splits_compound_column_names_and_normalizes_plurals():
    assert tokenize("customer_id") == ["customer", "id"]
    assert "customer" in tokenize("customers")
    assert "region" in tokenize("regions")


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


def test_retrieve_chunks_returns_empty_when_no_terms_match(monkeypatch):
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


def test_retrieve_chunks_for_datasets_ranks_only_selected_sheets(monkeypatch):
    calls = []
    chunks_by_dataset = {
        "sheet-a": [
            {
                "chunk_id": "a-1",
                "dataset_id": "sheet-a",
                "chunk_index": 0,
                "text": "revenue",
                "metadata": {},
            }
        ],
        "sheet-b": [
            {
                "chunk_id": "b-1",
                "dataset_id": "sheet-b",
                "chunk_index": 0,
                "text": "revenue revenue",
                "metadata": {},
            }
        ],
    }

    def get_chunks(dataset_id, database_path):
        calls.append(dataset_id)
        return chunks_by_dataset[dataset_id]

    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        get_chunks,
    )

    results = retrieve_chunks_for_datasets(
        "revenue",
        ["sheet-a", "sheet-b"],
        top_k=2,
    )

    assert calls == ["sheet-a", "sheet-b"]
    assert [item["chunk_id"] for item in results] == ["b-1", "a-1"]


def test_retrieve_chunks_for_datasets_includes_vector_only_matches(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "vectors.db"
    database.touch()
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: [],
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.search_similar_chunks",
        lambda query, dataset_ids, top_k, database_path: [
            {
                "chunk_id": "semantic-only",
                "dataset_id": "sheet-a",
                "chunk_index": 0,
                "text": "Solar lamp with rechargeable battery",
                "metadata": {"source_rows": [2]},
                "vector_similarity": 0.9,
            }
        ],
    )

    results = retrieve_chunks_for_datasets(
        "camping light",
        ["sheet-a"],
        database_path=database,
    )

    assert [result["chunk_id"] for result in results] == ["semantic-only"]
    assert results[0]["matched_terms"] == []
    assert results[0]["vector_similarity"] == 0.9


def test_retrieve_chunks_for_datasets_rejects_more_than_five():
    with pytest.raises(RetrievalError, match="at most five"):
        retrieve_chunks_for_datasets("sales", [str(index) for index in range(6)])


def test_graph_retrieval_follows_candidate_relationship_within_selected_sheets(
    monkeypatch,
):
    calls = []
    chunks_by_dataset = {
        "sheet-a": [
            {
                "chunk_id": "a-1",
                "dataset_id": "sheet-a",
                "chunk_index": 0,
                "text": "customer_name: Alice",
                "metadata": {},
            }
        ],
        "sheet-b": [
            {
                "chunk_id": "b-1",
                "dataset_id": "sheet-b",
                "chunk_index": 0,
                "text": "customer_id: 101",
                "metadata": {},
            }
        ],
    }

    def get_chunks(dataset_id, database_path):
        calls.append(dataset_id)
        return chunks_by_dataset[dataset_id]

    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        get_chunks,
    )
    graph_context = {
        "nodes": [
            {
                "id": "column:sheet-a:customer_name",
                "type": "Column",
                "dataset_id": "sheet-a",
                "label": "Customer Name",
                "sql_name": "customer_name",
            },
            {
                "id": "column:sheet-b:customer_id",
                "type": "Column",
                "dataset_id": "sheet-b",
                "label": "Customer ID",
                "sql_name": "customer_id",
            },
            {
                "id": "column:unselected:customer_id",
                "type": "Column",
                "dataset_id": "unselected",
                "label": "Customer ID",
                "sql_name": "customer_id",
            },
        ],
        "candidate_relationships": [
            {
                "left": {
                    "dataset_id": "sheet-a",
                    "column": "customer_name",
                },
                "right": {
                    "dataset_id": "sheet-b",
                    "column": "customer_id",
                },
                "status": "candidate",
            },
            {
                "left": {
                    "dataset_id": "sheet-a",
                    "column": "customer_name",
                },
                "right": {
                    "dataset_id": "unselected",
                    "column": "customer_id",
                },
                "status": "candidate",
            },
        ],
    }

    selected_graph = build_graph_retrieval_context(
        "name",
        ["sheet-a", "sheet-b"],
        graph_context,
    )
    results = retrieve_chunks_for_datasets(
        "name",
        ["sheet-a", "sheet-b"],
        top_k=2,
        graph_context=selected_graph,
    )

    assert calls == ["sheet-a", "sheet-b"]
    assert [result["chunk_id"] for result in results] == ["a-1", "b-1"]
    assert results[1]["matched_terms"] == []
    assert results[1]["graph_matches"][0]["sql_name"] == "customer_id"
    assert selected_graph["retrieval"]["related_columns"] == [
        graph_context["nodes"][1]
    ]


def test_graph_retrieval_finds_related_sheet_evidence_from_natural_language(
    monkeypatch,
):
    chunks_by_dataset = {
        "customers": [
            {
                "chunk_id": "customer-1",
                "dataset_id": "customers",
                "chunk_index": 0,
                "text": (
                    "customer_id: C-100\n"
                    "customer_name: Ada North\n"
                    "region: North"
                ),
                "metadata": {},
            }
        ],
        "transactions": [
            {
                "chunk_id": "transaction-1",
                "dataset_id": "transactions",
                "chunk_index": 0,
                "text": (
                    "transaction_id: T-1\n"
                    "customer_id: C-100\n"
                    "amount: 250"
                ),
                "metadata": {},
            }
        ],
    }
    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        lambda dataset_id, database_path: chunks_by_dataset[dataset_id],
    )
    graph_context = {
        "nodes": [
            {
                "id": "column:customers:customer_id",
                "type": "Column",
                "dataset_id": "customers",
                "label": "customer_id",
                "sql_name": "customer_id",
            },
            {
                "id": "column:customers:customer_name",
                "type": "Column",
                "dataset_id": "customers",
                "label": "customer_name",
                "sql_name": "customer_name",
            },
            {
                "id": "column:transactions:customer_id",
                "type": "Column",
                "dataset_id": "transactions",
                "label": "customer_id",
                "sql_name": "customer_id",
            },
            {
                "id": "column:transactions:amount",
                "type": "Column",
                "dataset_id": "transactions",
                "label": "amount",
                "sql_name": "amount",
            },
        ],
        "candidate_relationships": [
            {
                "left": {
                    "dataset_id": "customers",
                    "column": "customer_id",
                },
                "right": {
                    "dataset_id": "transactions",
                    "column": "customer_id",
                },
            }
        ],
    }
    selected_graph = build_graph_retrieval_context(
        "Which customers had the highest transaction amounts?",
        ["customers", "transactions"],
        graph_context,
    )

    results = retrieve_chunks_for_datasets(
        "Which customers had the highest transaction amounts?",
        ["customers", "transactions"],
        top_k=5,
        graph_context=selected_graph,
    )

    assert {result["dataset_id"] for result in results} == {
        "customers",
        "transactions",
    }


def test_retrieval_uses_graph_evidence_as_selected_sheet_source(monkeypatch):
    def unexpected_sql_chunk_lookup(**_kwargs):
        raise AssertionError("Graph evidence should be the retrieval source.")

    monkeypatch.setattr(
        "backend.app.services.retrieval.get_dataset_chunks",
        unexpected_sql_chunk_lookup,
    )
    results = retrieve_chunks_for_datasets(
        "sales revenue",
        ["sheet-a"],
        graph_context={
            "nodes": [
                {
                    "id": "evidence:sheet-a:chunk-1",
                    "type": "Evidence",
                    "dataset_id": "sheet-a",
                    "chunk_id": "chunk-1",
                    "chunk_index": 0,
                    "text": "Sales revenue was 250.",
                    "metadata": {"source_rows": [3]},
                },
                {
                    "id": "evidence:unselected:chunk-2",
                    "type": "Evidence",
                    "dataset_id": "unselected",
                    "chunk_id": "chunk-2",
                    "chunk_index": 0,
                    "text": "Sales revenue was 999.",
                    "metadata": {},
                },
            ]
        },
    )

    assert [result["chunk_id"] for result in results] == ["chunk-1"]
    assert results[0]["metadata"] == {"source_rows": [3]}
