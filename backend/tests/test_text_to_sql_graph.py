
"""Tests for the GraphSheet AI LangGraph Text-to-SQL workflow."""

from backend.app.services import text_to_sql_graph as graph_service


def test_empty_question_is_rejected():
    result = graph_service.ask_with_sql_graph(
        "",
        "a" * 32,
    )

    assert result["status"] == "error"
    assert result["error"] == "Question must not be empty."


def test_empty_dataset_id_is_rejected():
    result = graph_service.ask_with_sql_graph(
        "Count rows",
        "",
    )

    assert result["status"] == "error"
    assert result["error"] == "Dataset ID must not be empty."


def test_valid_question_executes_sql_service(monkeypatch):
    expected_result = {
        "status": "success",
        "dataset_id": "a" * 32,
        "question": "Count rows",
        "sql": 'SELECT COUNT(*) FROM "dataset_test"',
        "result": {"rows": [{"count": 2}]},
    }

    def fake_ask_with_sql(question, dataset_id):
        assert question == "Count rows"
        assert dataset_id == "a" * 32
        return expected_result

    monkeypatch.setattr(
        graph_service,
        "ask_with_sql",
        fake_ask_with_sql,
    )

    result = graph_service.ask_with_sql_graph(
        "Count rows",
        "a" * 32,
    )

    assert result["status"] == "success"
    assert result["result"] == expected_result
    assert result["result"]["result"]["rows"][0]["count"] == 2


def test_unexpected_sql_service_error_is_sanitized(monkeypatch):
    def fake_ask_with_sql(question, dataset_id):
        raise RuntimeError("Simulated SQL failure")

    monkeypatch.setattr(
        graph_service,
        "ask_with_sql",
        fake_ask_with_sql,
    )

    result = graph_service.ask_with_sql_graph(
        "Count rows",
        "a" * 32,
    )

    assert result["status"] == "error"
    assert result["error"] == "Text-to-SQL workflow failed."


def test_whitespace_question_is_rejected():
    result = graph_service.ask_with_sql_graph(
        "   ",
        "a" * 32,
    )

    assert result["status"] == "error"
    assert result["error"] == "Question must not be empty."
