
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
    """Replace Gemini's LangChain wrapper with a local fake model."""

    class FakeResponse:
        content = sql

    class FakeLLM:
        def __init__(self, **kwargs):
            self.config = kwargs

        def invoke(self, messages):
            return FakeResponse()

    monkeypatch.setattr(
        service,
        "ChatGoogleGenerativeAI",
        FakeLLM,
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


def test_missing_api_key_is_rejected(test_database, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(
        service.TextToSQLError,
        match="not configured",
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
