import json

import pytest

from backend.app.services import neo4j_store
from neo4j.exceptions import AuthError, ServiceUnavailable


class FakeResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    def consume(self):
        return None

    def data(self):
        return self.rows

    def single(self):
        return self.rows[0] if self.rows else {"ready": 1}


class FakeSession:
    def __init__(self, calls, driver):
        self.calls = calls
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def run(self, query, **parameters):
        self.calls.append((query, parameters))
        if "RETURN node.id AS id" in query:
            return FakeResult(
                [
                    {
                        "id": "dataset:1",
                        "type": "Dataset",
                        "label": "sales.csv",
                        "created_at": "2026-01-01",
                        "properties_json": json.dumps(
                            {"dataset_id": "a" * 32}
                        ),
                    }
                ]
            )
        if "RETURN edge.id AS id" in query:
            return FakeResult(
                [
                    {
                        "id": "edge:1",
                        "source": "dataset:1",
                        "target": "column:1",
                        "type": "HAS_COLUMN",
                        "properties_json": "{}",
                    }
                ]
            )
        return FakeResult()

    def execute_write(self, callback):
        self.driver.write_count += 1
        return callback(self)


class FakeDriver:
    def __init__(self, calls):
        self.calls = calls
        self.verify_count = 0
        self.write_count = 0
        self.closed = False
        self.connect_error = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.closed = True

    def verify_connectivity(self):
        self.verify_count += 1
        if self.connect_error:
            raise self.connect_error

    def session(self, database):
        assert database == "neo4j-test"
        return FakeSession(self.calls, self)


def configure_neo4j(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example.databases.neo4j.io")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j-test")


def test_neo4j_configuration_is_optional(monkeypatch):
    for key in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    assert not neo4j_store.is_configured()
    assert neo4j_store.graph_store_status() == "sqlite"
    assert neo4j_store.sync_graph("chat-1", {"nodes": [], "edges": []}) is False


def test_invalid_neo4j_uri_returns_structured_diagnostic(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "https://database.example")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")

    with pytest.raises(neo4j_store.Neo4jStoreError) as error:
        neo4j_store.check_connectivity()

    assert error.value.code == "NEO4J_INVALID_URI"
    assert "NEO4J_URI" in str(error.value)


def test_aura_uri_requires_encrypted_driver_scheme(monkeypatch):
    monkeypatch.setenv(
        "NEO4J_URI",
        "neo4j://example.databases.neo4j.io",
    )
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")

    with pytest.raises(neo4j_store.Neo4jStoreError) as error:
        neo4j_store.check_connectivity()

    assert error.value.code == "NEO4J_TLS_REQUIRED"


def test_connection_info_identifies_aura_without_exposing_host(monkeypatch):
    configure_neo4j(monkeypatch)

    info = neo4j_store.connection_info()

    assert info == {
        "configured": True,
        "deployment": "aura",
        "uri_scheme": "neo4j+s",
        "database": "neo4j-test",
    }


def test_partial_neo4j_configuration_fails_explicitly(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://example.databases.neo4j.io")
    monkeypatch.delenv("NEO4J_USERNAME", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    with pytest.raises(neo4j_store.Neo4jStoreError, match="together"):
        neo4j_store.sync_graph("chat-1", {"nodes": [], "edges": []})


def test_sync_graph_writes_scoped_nodes_and_edge_provenance(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: FakeDriver(calls),
    )
    graph = {
        "nodes": [
            {
                "id": "chat:1",
                "type": "Chat",
                "label": "Chat",
                "created_at": "2026-01-01",
            },
            {
                "id": "dataset:1",
                "type": "Dataset",
                "label": "sales.csv",
                "dataset_id": "a" * 32,
            },
        ],
        "edges": [
            {
                "id": "uses:1",
                "source": "chat:1",
                "target": "dataset:1",
                "type": "USES_DATASET",
            }
        ],
    }

    assert neo4j_store.sync_graph("chat-1", graph) is True
    queries = [query for query, _ in calls]
    assert any(
        "REQUIRE (n.scope_id, n.id) IS UNIQUE" in query
        for query in queries
    )
    assert any(
        "REQUIRE (e.scope_id, e.id) IS UNIQUE" in query
        for query in queries
    )
    assert any("UNWIND $nodes AS row" in query for query in queries)
    assert any("UNWIND $edges AS row" in query for query in queries)
    assert any(
        "OPTIONAL MATCH (edge)-[link]-()" in query for query in queries
    )
    edge_write = next(
        parameters
        for query, parameters in calls
        if "UNWIND $edges AS row" in query
    )
    assert edge_write["edges"][0]["relationship"] == "USES_DATASET"
    assert edge_write["edges"][0]["scope_id"] == "chat-1"
    node_write = next(
        parameters
        for query, parameters in calls
        if "UNWIND $nodes AS row" in query
    )
    assert json.loads(node_write["nodes"][1]["properties_json"]) == {
        "dataset_id": "a" * 32
    }


def test_driver_is_reused_and_connectivity_is_verified_once(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    drivers = []

    def create_driver(*_args, **_kwargs):
        driver = FakeDriver(calls)
        drivers.append(driver)
        return driver

    monkeypatch.setattr(neo4j_store.GraphDatabase, "driver", create_driver)

    assert neo4j_store.check_connectivity()["ready"] is True
    assert neo4j_store.check_connectivity()["ready"] is True

    assert len(drivers) == 1
    assert drivers[0].verify_count == 1


def test_unavailable_tls_connection_is_classified_and_throttled(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    driver = FakeDriver(calls)
    driver.connect_error = ConnectionResetError("TLS peer reset")
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: driver,
    )

    with pytest.raises(neo4j_store.Neo4jStoreError) as first_error:
        neo4j_store.check_connectivity()
    with pytest.raises(neo4j_store.Neo4jStoreError) as cached_error:
        neo4j_store.check_connectivity()

    assert first_error.value.code == "NEO4J_TLS_CONNECTION_FAILED"
    assert cached_error.value.code == first_error.value.code
    assert cached_error.value.retry_after_seconds is not None
    assert driver.verify_count == 1


def test_routing_failure_retains_underlying_tls_diagnostic():
    tls_failure = ConnectionResetError("TLS peer reset")
    routing_failure = ServiceUnavailable(
        "Unable to retrieve routing information"
    )
    routing_failure.__cause__ = tls_failure

    error = neo4j_store._error_for(routing_failure)

    assert error.code == "NEO4J_TLS_CONNECTION_FAILED"
    assert "TLS" in str(error)


def test_authentication_diagnostic_does_not_expose_driver_error_text():
    sensitive_error_text = "password=do-not-leak"

    error = neo4j_store._error_for(AuthError(sensitive_error_text))

    assert error.code == "NEO4J_AUTHENTICATION_FAILED"
    assert sensitive_error_text not in str(error)


def test_sync_is_idempotent_and_preserves_scope(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    drivers = []

    def create_driver(*_args, **_kwargs):
        driver = FakeDriver(calls)
        drivers.append(driver)
        return driver

    monkeypatch.setattr(neo4j_store.GraphDatabase, "driver", create_driver)
    graph = {
        "nodes": [
            {"id": "dataset:1", "type": "Dataset", "label": "Sales"},
            {"id": "column:1", "type": "Column", "label": "Revenue"},
        ],
        "edges": [
            {
                "id": "edge:1",
                "source": "dataset:1",
                "target": "column:1",
                "type": "HAS_COLUMN",
            }
        ],
    }

    assert neo4j_store.sync_graph("dataset:a", graph)
    assert neo4j_store.sync_graph("dataset:a", graph)

    assert len(drivers) == 1
    assert drivers[0].write_count == 2
    node_write = next(
        parameters
        for query, parameters in calls
        if "UNWIND $nodes AS row" in query
    )
    assert {node["scope_id"] for node in node_write["nodes"]} == {
        "dataset:a"
    }
    assert sum(
        "CREATE CONSTRAINT graph_node_scope_id IF NOT EXISTS" in query
        for query, _ in calls
    ) == 2
    assert sum("MERGE (edge:GraphEdge" in query for query, _ in calls) == 2
    assert sum(
        "OPTIONAL MATCH (edge)-[link]-()" in query
        for query, _ in calls
    ) == 2


def test_get_graph_reads_nodes_and_edges_from_requested_scope(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    driver = FakeDriver(calls)
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: driver,
    )

    graph = neo4j_store.get_graph("chat:one")

    assert graph["scope_id"] == "chat:one"
    assert graph["nodes"][0]["dataset_id"] == "a" * 32
    assert graph["edges"][0]["type"] == "HAS_COLUMN"
    assert all(
        parameters.get("scope_id") == "chat:one"
        for query, parameters in calls
        if "MATCH (node:GraphNode" in query
        or "MATCH (source:GraphNode" in query
    )


def test_delete_graphs_uses_one_transaction_for_selected_scopes(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    driver = FakeDriver(calls)
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: driver,
    )

    neo4j_store.delete_graphs(["dataset:a", "chat:one", "dataset:a"])

    assert driver.write_count == 1
    delete_queries = [
        (query, parameters)
        for query, parameters in calls
        if "UNWIND $scope_ids AS scope_id" in query
    ]
    assert len(delete_queries) == 2
    assert all(
        parameters["scope_ids"] == ["dataset:a", "chat:one"]
        for _, parameters in delete_queries
    )


def test_close_driver_closes_the_shared_driver(monkeypatch):
    configure_neo4j(monkeypatch)
    calls = []
    driver = FakeDriver(calls)
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: driver,
    )

    neo4j_store.initialize_driver()
    neo4j_store.close_driver()

    assert driver.closed


def test_sync_graph_batches_large_node_and_edge_writes(monkeypatch):
    configure_neo4j(monkeypatch)
    monkeypatch.setattr(neo4j_store, "WRITE_BATCH_SIZE", 2)
    calls = []
    monkeypatch.setattr(
        neo4j_store.GraphDatabase,
        "driver",
        lambda *_args, **_kwargs: FakeDriver(calls),
    )
    graph = {
        "nodes": [
            {"id": f"node:{index}", "type": "Dataset", "label": str(index)}
            for index in range(3)
        ],
        "edges": [
            {
                "id": f"edge:{index}",
                "source": f"node:{index}",
                "target": f"node:{(index + 1) % 3}",
                "type": "RELATED_TO",
            }
            for index in range(3)
        ],
    }

    assert neo4j_store.sync_graph("batch-test", graph)
    node_batches = [
        parameters["nodes"]
        for query, parameters in calls
        if "UNWIND $nodes AS row" in query
    ]
    edge_batches = [
        parameters["edges"]
        for query, parameters in calls
        if "UNWIND $edges AS row" in query
    ]
    assert [len(batch) for batch in node_batches] == [2, 1]
    assert [len(batch) for batch in edge_batches] == [2, 1]
