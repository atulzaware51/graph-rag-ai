import pytest

from backend.app.services import answer_service
from backend.app.services import text_to_sql_service
from backend.app.services.answer_service import (
    AnswerServiceError,
    _deterministic_answer,
    answer_question,
)
from backend.app.services.dataset_store import store_spreadsheet
from backend.app.services.llm_provider import LLMProviderError


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


def test_answer_question_uses_sql_fallback_when_chunks_do_not_match(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "offerings.csv"
    source.write_text(
        "Offering,Release Date\nAlpha,2025-01-01\nBeta,2026-03-15\n",
        encoding="utf-8",
    )
    database = tmp_path / "analyst-fallback.db"
    stored = store_spreadsheet(source, "offerings.csv", database)
    monkeypatch.setattr(answer_service, "retrieve_chunks", lambda **_kwargs: [])
    monkeypatch.setattr(
        text_to_sql_service,
        "generate_text",
        lambda **_kwargs: (
            f'SELECT * FROM "{stored["table_name"]}" LIMIT 1',
            "gemini",
        ),
    )
    answer_prompts = []
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **kwargs: (
            answer_prompts.append(kwargs)
            or "The selected sheet includes Alpha, released on 2025-01-01.",
            "groq",
        ),
    )

    result = answer_question(
        query="Which offering appears in the selected sheet?",
        dataset_id=stored["dataset_id"],
        graph_context={"nodes": [], "edges": []},
        database_path=database,
    )

    assert result["status"] == "success"
    assert result["answer_method"] == "sql_assisted_llm"
    assert result["answer_mode"] == "sql_assisted_llm"
    assert result["provider"] == "groq"
    assert result["evidence_count"] == 1
    assert "Alpha" in result["answer"]
    assert result["sources"] == []
    assert result["structured_answer"]["sections"][0]["table"]["rows"]
    assert result["sql_sources"] == [
        {
            "dataset_id": stored["dataset_id"],
            "filename": "offerings.csv",
            "sheet_name": None,
            "table_name": stored["table_name"],
        }
    ]
    assert "Alpha" in answer_prompts[0]["user_prompt"]


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
    assert result["answer_mode"] == "llm"
    assert result["provider"] == "groq"
    assert result["evidence_count"] == 1
    assert result["sources"][0]["chunk_id"] == "chunk-1"
    assert "January sales revenue was 50000 rupees." in prompts[0][1]


def test_filtered_count_uses_sql_instead_of_unfiltered_deterministic_count(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        answer_service,
        "_deterministic_answer",
        lambda *args, **kwargs: {"answer": "incorrect unfiltered count"},
    )
    monkeypatch.setattr(
        answer_service,
        "_answer_from_sql_fallback",
        lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or {"status": "success", "answer_method": "sql_assisted_llm"}
        ),
    )

    result = answer_question(
        query="How many products are under 100?",
        dataset_id="dataset-1",
        graph_context={"nodes": [], "edges": []},
    )

    assert result["answer_method"] == "sql_assisted_llm"
    assert len(calls) == 1


def test_sql_result_uses_readable_file_label_instead_of_dataset_id(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "products.csv"
    source.write_text("Product,Price\nLamp,120\n", encoding="utf-8")
    database = tmp_path / "labeled-sql.db"
    stored = store_spreadsheet(source, "products.csv", database)
    monkeypatch.setattr(
        answer_service,
        "ask_with_sql",
        lambda *args, **kwargs: {
            "sql": f'SELECT "{stored["dataset_id"]}" AS sheet',
            "result": {
                "columns": ["sheet", "row_count"],
                "rows": [
                    {
                        "sheet": stored["dataset_id"],
                        "row_count": 1,
                    }
                ],
                "row_count": 1,
            },
        },
    )
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **_kwargs: ("The sheet contains one row.", "groq"),
    )

    result = answer_service._answer_from_sql_fallback(
        "How many rows?",
        "How many rows?",
        [stored["dataset_id"]],
        None,
        None,
        database,
    )

    assert result["sql_result"]["rows"][0]["sheet"] == "products.csv"
    assert result["structured_answer"]["sections"][0]["table"]["rows"] == [
        {"sheet": "products.csv", "row_count": 1}
    ]
    assert result["sql_sources"][0]["filename"] == "products.csv"


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


def test_chat_answer_uses_chunks_when_graph_has_no_matches(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks_for_datasets",
        lambda **_kwargs: sample_chunks(),
    )
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **_kwargs: ("Grounded answer from chunks.", "gemini"),
    )

    result = answer_service.answer_question(
        query="Compare revenue",
        dataset_ids=["dataset-1"],
        graph_context={"nodes": [], "edges": []},
    )

    assert result["answer"] == "Grounded answer from chunks."
    assert result["sources"][0]["chunk_id"] == "chunk-1"
    assert result["graph_retrieval"]["graph_available"] is False
    assert result["graph_status"] == "unknown"


def test_deterministic_fallback_answers_maximum_from_selected_dataset(tmp_path):
    source = tmp_path / "products.csv"
    source.write_text(
        "Product,Discount Price,Price,Ratings\n"
        "Compact,90,100,4\n"
        "Premium,210,250,9\n"
        "Standard,150,175,6\n",
        encoding="utf-8",
    )
    database = tmp_path / "fallback.db"
    stored = store_spreadsheet(
        source,
        "products.csv",
        database,
        chunks=[],
    )

    result = _deterministic_answer(
        "What is the maximum product price?",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert result is not None
    assert "250" in result["answer"]
    assert "Premium" in result["answer"]
    assert result["queries"][0]["dataset"] == "products.csv"


def test_answer_question_uses_deterministic_fallback_after_provider_failure(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "products.csv"
    source.write_text(
        "Product,Price\nCompact,100\nPremium,250\n",
        encoding="utf-8",
    )
    database = tmp_path / "fallback-chat.db"
    stored = store_spreadsheet(
        source,
        "products.csv",
        database,
        chunks=[
            {
                "chunk_id": "premium-row",
                "text": "Product: Premium\nPrice: 250",
                "metadata": {"source_rows": [2]},
            }
        ],
    )
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks_for_datasets",
        lambda **_kwargs: [
            {
                "chunk_id": "premium-row",
                "dataset_id": stored["dataset_id"],
                "chunk_index": 0,
                "text": "Product: Premium\nPrice: 250",
                "metadata": {"source_rows": [2]},
                "score": 1,
                "matched_terms": ["price"],
                "graph_matches": [],
            }
        ],
    )
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **_kwargs: (_ for _ in ()).throw(
            LLMProviderError(
                "Providers unavailable.",
                code="LLM_PROVIDERS_UNAVAILABLE",
                retryable=True,
            )
        ),
    )

    result = answer_question(
        "What is the maximum product price?",
        dataset_ids=[stored["dataset_id"]],
        graph_context={"nodes": [], "edges": []},
        database_path=database,
    )

    assert result["answer_method"] == "deterministic_sql"
    assert "provider_error" not in result
    assert "Premium" in result["answer"]
    assert result["sources"] == []
    assert result["sql_sources"][0]["filename"] == "products.csv"


def test_deterministic_fallback_counts_rows_by_selected_category(tmp_path):
    source = tmp_path / "products-by-category.csv"
    source.write_text(
        "Main Category,Product\n"
        "Appliances,Compact\n"
        "Appliances,Premium\n"
        "Furniture,Desk\n",
        encoding="utf-8",
    )
    database = tmp_path / "category-counts.db"
    stored = store_spreadsheet(
        source,
        "products-by-category.csv",
        database,
        chunks=[],
    )

    result = _deterministic_answer(
        "How many products are in each main category?",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert result is not None
    assert "Appliances: 2" in result["answer"]
    assert "Furniture: 1" in result["answer"]


def test_deterministic_answer_returns_structured_ranking_and_tables(tmp_path):
    source = tmp_path / "products.csv"
    source.write_text(
        "Product,Category,Sales,Price\n"
        "Laptop,Electronics,300,1200\n"
        "Tablet,Electronics,200,600\n"
        "Chair,Furniture,50,100\n",
        encoding="utf-8",
    )
    database = tmp_path / "structured-answers.db"
    stored = store_spreadsheet(source, "products.csv", database)

    ranking = _deterministic_answer(
        "Show the top 2 products by price",
        [stored["dataset_id"]],
        database_path=database,
    )
    grouped = _deterministic_answer(
        "Show total sales by category",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert ranking["structured_answer"]["answer_type"] == "ranking"
    assert len(ranking["structured_answer"]["sections"][0]["table"]["rows"]) == 2
    assert ranking["structured_answer"]["sections"][0]["table"]["rows"][0]["product"] == "Laptop"
    assert grouped["structured_answer"]["answer_type"] == "grouped_analysis"
    assert {
        row["category"]: row["value"]
        for row in grouped["structured_answer"]["sections"][0]["table"]["rows"]
    } == {"Electronics": 500, "Furniture": 50}


def test_deterministic_answer_builds_category_and_subcategory_tables(tmp_path):
    source = tmp_path / "products.csv"
    source.write_text(
        "Main Category,Subcategory,Product\n"
        "Appliances,Refrigerators,ColdBox\n"
        "Appliances,Ovens,HeatBox\n"
        "Furniture,Chairs,SeatOne\n",
        encoding="utf-8",
    )
    database = tmp_path / "subcategories.db"
    stored = store_spreadsheet(source, "products.csv", database)

    result = _deterministic_answer(
        "Show product counts in each main category and subcategory",
        [stored["dataset_id"]],
        database_path=database,
    )

    section = result["structured_answer"]["sections"][0]
    assert section["table"]["rows"] == [
        {"category": "Appliances", "count": 2},
        {"category": "Furniture", "count": 1},
    ]
    assert section["subtables"][0]["table"]["rows"] == [
        {"subcategory": "Refrigerators", "count": 1},
        {"subcategory": "Ovens", "count": 1},
    ]


def test_question_uses_deterministic_calculation_without_retrieved_chunks(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "products.csv"
    source.write_text(
        "Product,Price\nCompact,100\nPremium,250\n",
        encoding="utf-8",
    )
    database = tmp_path / "no-chunks.db"
    stored = store_spreadsheet(source, "products.csv", database)
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks_for_datasets",
        lambda **_kwargs: [],
    )

    result = answer_service.answer_question(
        "What is the maximum product price?",
        dataset_ids=[stored["dataset_id"]],
        graph_context={"nodes": [], "edges": []},
        database_path=database,
    )

    assert result["status"] == "success"
    assert result["answer_method"] == "deterministic_sql"
    assert result["structured_answer"]["sections"][0]["table"]["rows"][0]["price"] == 250
    assert result["sources"] == []


def test_answer_question_parses_structured_model_response(monkeypatch):
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks",
        lambda **_kwargs: sample_chunks(),
    )
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **_kwargs: (
            '{"answer_type":"lookup","summary":"Found the matching record.",'
            '"sections":[{"title":"Result","table":{"columns":'
            '[{"key":"product","label":"Product"}],"rows":'
            '[{"product":"Premium"}]}}],'
            '"evidence":[{"chunk_id":"made-up-chunk"}]}',
            "gemini",
        ),
    )

    result = answer_service.answer_question(
        query="Find the product",
        dataset_id="dataset-1",
    )

    assert result["answer"] == "Found the matching record."
    assert result["structured_answer"]["sections"][0]["table"]["rows"] == [
        {"product": "Premium"}
    ]
    assert result["structured_answer"]["evidence"][0]["chunk_id"] == "chunk-1"


def test_structured_answer_normalizes_object_keyed_table_rows():
    parsed = answer_service._parse_structured_answer(
        '{"answer_type":"text","summary":"Schema generated.",'
        '"sections":[{"title":"DDL","table":{"columns":'
        '[{"key":"table_name","label":"Table Name"}],"rows":'
        '{"row_1":{"table_name":"Students"},'
        '"row_2":{"table_name":"AcademicPerformance"}}}}]}'
    )

    assert parsed is not None
    assert parsed["sections"][0]["table"]["rows"] == [
        {"table_name": "Students"},
        {"table_name": "AcademicPerformance"},
    ]


def test_followup_question_uses_recent_history_for_retrieval(monkeypatch):
    seen_queries = []
    monkeypatch.setattr(
        answer_service,
        "retrieve_chunks_for_datasets",
        lambda **kwargs: seen_queries.append(kwargs["query"]) or sample_chunks(),
    )
    monkeypatch.setattr(
        answer_service,
        "generate_text",
        lambda **_kwargs: ("The Premium product.", "gemini"),
    )

    answer_service.answer_question(
        query="Which of these has the highest discount?",
        dataset_ids=["dataset-1"],
        graph_context={"nodes": [], "edges": []},
        conversation_history=[
            {"role": "user", "content": "Show the most expensive products."},
            {"role": "assistant", "content": "Premium and Compact were returned."},
        ],
    )

    assert len(seen_queries) == 1
    assert "Premium and Compact" in seen_queries[0]


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
