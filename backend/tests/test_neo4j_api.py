from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_api
from backend.app.api import datasets as datasets_api
from backend.app.api import graph as graph_api
from backend.app.main import app
from backend.app.services.neo4j_store import Neo4jStoreError


client = TestClient(app)


def test_health_is_liveness_and_does_not_claim_neo4j_ready():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["checks"]["sqlite"]["status"] in {
        "available",
        "not_initialized",
        "unavailable",
    }
    assert response.json()["checks"]["neo4j"]["status"] == "not_checked"


def test_health_reports_sqlite_readiness_without_creating_database(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "not-created.db"
    monkeypatch.setattr("backend.app.main.DEFAULT_DATABASE", database)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["checks"]["sqlite"] == {
        "available": None,
        "status": "not_initialized",
    }
    assert not database.exists()


def test_health_reports_sqlite_as_available(monkeypatch, tmp_path):
    import sqlite3

    database = tmp_path / "ready.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE health_probe (value INTEGER)")
    monkeypatch.setattr("backend.app.main.DEFAULT_DATABASE", database)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["checks"]["sqlite"] == {
        "available": True,
        "status": "available",
    }


def test_neo4j_readiness_reports_structured_tls_failure(monkeypatch):
    error = Neo4jStoreError(
        "TLS handshake failed.",
        code="NEO4J_TLS_CONNECTION_FAILED",
        retry_after_seconds=10,
    )
    monkeypatch.setattr(
        "backend.app.main.check_connectivity",
        lambda: (_ for _ in ()).throw(error),
    )

    response = client.get("/ready/neo4j")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert response.json()["error"]["code"] == (
        "NEO4J_TLS_CONNECTION_FAILED"
    )


def test_neo4j_readiness_reports_missing_configuration():
    response = client.get("/ready/neo4j")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NEO4J_NOT_CONFIGURED"


def test_application_lifespan_closes_shared_driver(monkeypatch):
    closed = []
    monkeypatch.setattr(
        "backend.app.main.close_driver",
        lambda: closed.append(True),
    )

    with TestClient(app):
        pass

    assert closed == [True]


def test_graph_api_uses_the_persistent_graph_store(monkeypatch):
    sqlite_graph = {
        "status": "success",
        "node_count": 1,
        "edge_count": 0,
        "nodes": [{"id": "dataset:one"}],
        "edges": [],
        "candidate_relationships": [{"status": "candidate"}],
    }
    monkeypatch.setattr(
        graph_api,
        "build_graph_from_database",
        lambda: sqlite_graph.copy(),
    )
    response = client.get("/graph/")

    assert response.status_code == 200
    body = response.json()
    assert body["nodes"] == sqlite_graph["nodes"]
    assert body["candidate_relationships"] == (
        sqlite_graph["candidate_relationships"]
    )
    assert body["graph_store"] == "sqlite"


def test_graph_api_does_not_read_graph_from_neo4j(monkeypatch):
    monkeypatch.setattr(
        graph_api,
        "build_graph_from_database",
        lambda: {
            "status": "success",
            "nodes": [{"id": "dataset:one"}],
            "edges": [],
        },
    )
    response = client.get("/graph/")

    assert response.status_code == 200
    assert response.json()["nodes"] == [{"id": "dataset:one"}]
    assert response.json()["graph_store"] == "sqlite"


def test_conversation_graph_endpoint_reports_sqlite_fallback(monkeypatch):
    monkeypatch.setattr(
        conversations_api,
        "get_conversation",
        lambda _conversation_id: {
            "conversation_id": "chat-1",
            "selected_dataset_ids": [],
            "events": [],
        },
    )
    monkeypatch.setattr(
        conversations_api,
        "validate_dataset_ids",
        lambda _dataset_ids: [],
    )
    monkeypatch.setattr(
        conversations_api,
        "_selected_graph_context",
        lambda *_args, **_kwargs: {
            "conversation_id": "chat-1",
            "nodes": [{"id": "chat:one", "type": "Chat"}],
            "edges": [],
            "candidate_relationships": [],
            "graph_store": "sqlite",
        },
    )

    response = client.get("/conversations/chat-1/graph")

    assert response.status_code == 200
    assert response.json()["graph"]["nodes"] == [
        {"id": "chat:one", "type": "Chat"}
    ]
    assert response.json()["graph"]["graph_store"] == "sqlite"


def test_dataset_delete_returns_structured_neo4j_error(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "delete_spreadsheet",
        lambda _dataset_id: (_ for _ in ()).throw(
            Neo4jStoreError(
                "Neo4j TLS connection failed.",
                code="NEO4J_TLS_CONNECTION_FAILED",
            )
        ),
    )

    response = client.delete(f"/datasets/{'a' * 32}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == (
        "NEO4J_TLS_CONNECTION_FAILED"
    )
