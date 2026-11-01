
import sqlite3

import pytest

from backend.app.services import text_to_sql_service as service


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    database = tmp_path / "test.db"
    table_name = "dataset_" + "a" * 32

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                table_name TEXT NOT NULL UNIQUE
            )
            """
        )

        connection.execute(
            f"""
            CREATE TABLE "{table_name}" (
                product TEXT,
                quantity INTEGER,
                price INTEGER
            )
            """
        )

        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            ("a" * 32, table_name),
        )

        connection.executemany(
            f'INSERT INTO "{table_name}" VALUES (?, ?, ?)',
            [
                ("Laptop", 2, 50000),
                ("Mouse", 5, 500),
            ],
        )

    monkeypatch.setattr(
        service,
        "DEFAULT_DATABASE",
        database,
        raising=False,
    )

    monkeypatch.setattr(
        service,
        "get_dataset_details",
        lambda dataset_id: {
            "dataset_id": dataset_id,
            "table_name": table_name,
        },
    )

    return database


def mock_langchain(monkeypatch, sql):
    """Replace provider generation with a local SQL response."""
    monkeypatch.setattr(
        service,
        "generate_text",
        lambda **_kwargs: (sql, "gemini"),
    )

    monkeypatch.setattr(
        service,
        "load_dotenv",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_ask_with_sql_returns_results(test_database, monkeypatch):
    table_name = "dataset_" + "a" * 32

    sql = (
        f'SELECT SUM(quantity * price) AS revenue '
        f'FROM "{table_name}"'
    )

    mock_langchain(monkeypatch, sql)

    result = service.ask_with_sql(
        "Calculate total revenue",
        "a" * 32,
    )

    assert result["status"] == "success"
    assert result["dataset_id"] == "a" * 32
    assert result["question"] == "Calculate total revenue"
    assert result["sql"] == sql
    assert result["result"]["rows"][0]["revenue"] == 102500


def test_empty_question_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        service.ask_with_sql("", "a" * 32)


@pytest.mark.parametrize("dataset_ids", [[], ["a" * 32, ""], "a" * 32])
def test_invalid_dataset_id_lists_are_rejected(dataset_ids):
    with pytest.raises(ValueError, match="dataset"):
        service.ask_with_sql(
            "Count rows",
            dataset_ids=dataset_ids,
        )


def test_missing_api_key_is_rejected(test_database, monkeypatch):
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(
        service.TextToSQLError,
        match="No configured LLM API key",
    ):
        service.ask_with_sql("Count rows", "a" * 32)


def test_generated_write_query_is_rejected(
    test_database,
    monkeypatch,
):
    table_name = "dataset_" + "a" * 32
    sql = f'DELETE FROM "{table_name}"'

    mock_langchain(monkeypatch, sql)

    with pytest.raises(
        service.TextToSQLError,
        match="rejected",
    ):
        service.ask_with_sql(
            "Delete all rows",
            "a" * 32,
        )


def test_ask_with_sql_uses_selected_tables_and_graph_context(
    test_database,
    monkeypatch,
):
    first_id = "a" * 32
    second_id = "b" * 32
    unselected_id = "e" * 32
    first_table = f"dataset_{first_id}"
    second_table = f"dataset_{second_id}"
    unselected_table = f"dataset_{unselected_id}"
    with sqlite3.connect(test_database) as connection:
        connection.execute(
            f'CREATE TABLE "{second_table}" (quantity INTEGER)'
        )
        connection.execute(
            f'INSERT INTO "{second_table}" VALUES (3)'
        )
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            (second_id, second_table),
        )
        connection.execute(
            f'CREATE TABLE "{unselected_table}" (quantity INTEGER)'
        )
        connection.execute(
            f'INSERT INTO "{unselected_table}" VALUES (999)'
        )
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            (unselected_id, unselected_table),
        )

    monkeypatch.setattr(
        service,
        "get_dataset_details",
        lambda dataset_id: {
            "dataset_id": dataset_id,
            "table_name": (
                first_table if dataset_id == first_id else second_table
            ),
            "original_filename": f"{dataset_id[:1]}.csv",
        },
    )
    sql = (
        f'SELECT SUM(quantity) AS total FROM "{first_table}" '
        f'UNION ALL SELECT SUM(quantity) FROM "{second_table}"'
    )
    mock_langchain(monkeypatch, sql)

    def graph_checking_generate(*, system_prompt, user_prompt):
        assert first_table in system_prompt
        assert second_table in system_prompt
        assert '"status":"candidate"' in system_prompt
        assert '"matched_relationships"' in system_prompt
        assert unselected_id not in system_prompt
        assert "private-sample-value" not in system_prompt
        return sql, "gemini"

    monkeypatch.setattr(service, "generate_text", graph_checking_generate)
    result = service.ask_with_sql(
        "Compare total quantities",
        dataset_ids=[first_id, second_id],
        graph_context={
            "nodes": [
                {
                    "id": f"column:{first_id}:quantity",
                    "type": "Column",
                    "dataset_id": first_id,
                    "label": "quantities",
                    "sql_name": "quantity",
                    "sample_values": ["private-sample-value"],
                },
                {
                    "id": f"column:{second_id}:quantity",
                    "type": "Column",
                    "dataset_id": second_id,
                    "label": "quantity",
                    "sql_name": "quantity",
                },
                {
                    "id": f"column:{unselected_id}:quantity",
                    "type": "Column",
                    "dataset_id": unselected_id,
                    "label": "quantity",
                    "sql_name": "quantity",
                },
            ],
            "candidate_relationships": [
                {
                    "left": {
                        "dataset_id": first_id,
                        "column": "quantity",
                    },
                    "right": {
                        "dataset_id": second_id,
                        "column": "quantity",
                    },
                    "status": "candidate",
                    "confidence": 0.9,
                },
                {
                    "left": {
                        "dataset_id": first_id,
                        "column": "quantity",
                    },
                    "right": {
                        "dataset_id": unselected_id,
                        "column": "quantity",
                    },
                    "status": "candidate",
                    "confidence": 1.0,
                },
            ],
        },
    )

    assert result["dataset_ids"] == [first_id, second_id]
    assert result["result"]["rows"] == [{"total": 7}, {"total": 3}]
    assert len(result["graph_guidance"]["matched_relationships"]) == 1

    mock_langchain(
        monkeypatch,
        f'SELECT quantity FROM "{unselected_table}"',
    )
    with pytest.raises(service.TextToSQLError, match="rejected"):
        service.ask_with_sql(
            "Read an unselected table",
            dataset_ids=[first_id],
        )
