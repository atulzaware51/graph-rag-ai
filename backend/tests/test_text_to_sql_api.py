
"""API tests for the LangGraph Text-to-SQL endpoint."""

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app
from backend.app.api import datasets as datasets_api


client = TestClient(app)

VALID_DATASET_ID = "a" * 32


@pytest.fixture(autouse=True)
def fake_selected_graph(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "build_graph_for_scope",
        lambda *_args, **_kwargs: {"nodes": [], "edges": []},
    )


def test_ask_sql_success(monkeypatch):
    """A valid request returns SQL and its results."""

    expected_response = {
        "status": "success",
        "dataset_id": VALID_DATASET_ID,
        "question": "Calculate total revenue",
        "sql": "SELECT SUM(quantity * price) AS total_revenue",
        "result": {
            "status": "success",
            "columns": ["total_revenue"],
            "row_count": 1,
            "rows": [{"total_revenue": 102500}],
        },
    }

    def fake_ask_with_sql_graph(question, dataset_id, graph_context=None):
        assert question == "Calculate total revenue"
        assert dataset_id == VALID_DATASET_ID
        assert graph_context == {"nodes": [], "edges": []}

        return {
            "status": "success",
            "result": expected_response,
        }

    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        fake_ask_with_sql_graph,
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Calculate total revenue"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["dataset_id"] == VALID_DATASET_ID
    assert body["result"]["rows"][0]["total_revenue"] == 102500


def test_ask_sql_empty_question():
    """An empty question is rejected by request validation."""

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": ""},
    )

    assert response.status_code == 422


def test_ask_sql_missing_question():
    """A request without the required question is rejected."""

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={},
    )

    assert response.status_code == 422


def test_ask_sql_unknown_dataset(monkeypatch):
    """An unknown dataset returns HTTP 404."""

    def fake_ask_with_sql_graph(question, dataset_id, graph_context=None):
        return {
            "status": "error",
            "error": "Dataset not found.",
        }

    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        fake_ask_with_sql_graph,
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Count rows"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Dataset not found."


def test_ask_sql_rejected_query(monkeypatch):
    """Rejected generated SQL returns an upstream/service error."""

    error_message = (
        "Generated SQL was rejected: write statements are prohibited."
    )

    def fake_ask_with_sql_graph(question, dataset_id, graph_context=None):
        return {
            "status": "error",
            "error": error_message,
        }

    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        fake_ask_with_sql_graph,
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Delete all rows"},
    )

    assert response.status_code == 502
    assert "rejected" in response.json()["detail"].lower()



def test_ask_sql_missing_provider_configuration(monkeypatch):
    """Missing LLM provider configuration returns a server error."""

    def fake_ask_with_sql_graph(question, dataset_id, graph_context=None):
        return {
            "status": "error",
            "error": "No configured LLM API key was found.",
            "error_code": "LLM_PROVIDER_NOT_CONFIGURED",
            "retryable": False,
        }

    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        fake_ask_with_sql_graph,
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Count rows"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Text-to-SQL service is not configured."
    )


def test_ask_sql_provider_exhaustion_is_structured_and_retryable(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        lambda **_kwargs: {
            "status": "error",
            "error": "Configured LLM providers are temporarily unavailable.",
            "error_code": "LLM_PROVIDERS_UNAVAILABLE",
            "retryable": True,
        },
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Count rows"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "LLM_PROVIDERS_UNAVAILABLE",
        "message": "Configured LLM providers are temporarily unavailable.",
        "retryable": True,
    }


def test_ask_sql_malformed_workflow_response(monkeypatch):
    """An invalid success response returns HTTP 500."""

    def fake_ask_with_sql_graph(question, dataset_id, graph_context=None):
        return {"status": "success"}

    monkeypatch.setattr(
        datasets_api,
        "ask_with_sql_graph",
        fake_ask_with_sql_graph,
    )

    response = client.post(
        f"/datasets/{VALID_DATASET_ID}/ask-sql",
        json={"question": "Count rows"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Invalid response from the Text-to-SQL workflow."
    )
