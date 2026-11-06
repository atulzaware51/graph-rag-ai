
import sqlite3

import pytest

from backend.app.services.graph_service import (
    GraphServiceError,
    build_graph_for_scope,
    build_graph_from_database,
)
from backend.app.services import graph_service, neo4j_store
from backend.app.services.dataset_store import store_spreadsheet


def test_build_graph_from_empty_database(tmp_path):
    database_path = tmp_path / "empty.db"

    # An empty SQLite file with no application tables.
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                column_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE dataset_columns (
                dataset_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                original_name TEXT NOT NULL,
                sql_name TEXT NOT NULL,
                data_type TEXT NOT NULL,
                null_count INTEGER NOT NULL
            )
            """
        )

    result = build_graph_from_database(database_path)

    assert result["status"] == "success"
    assert result["dataset_count"] == 0
    assert result["node_count"] == 0
    assert result["edge_count"] == 0
    assert result["nodes"] == []
    assert result["edges"] == []


def test_graph_preserves_column_types_from_dataset_profile(tmp_path):
    source = tmp_path / "profile.csv"
    source.write_text("Product Name,Quantity\nLaptop,2\n", encoding="utf-8")
    database = tmp_path / "graph.db"
    store_spreadsheet(source, "profile.csv", database)

    result = build_graph_from_database(database)
    columns = [node for node in result["nodes"] if node["type"] == "Column"]

    assert {node["data_type"] for node in columns} == {"TEXT", "INTEGER"}
    quantity = next(node for node in columns if node["sql_name"] == "quantity")
    assert quantity["sample_values"] == [2]
    assert quantity["unique_count"] == 1
    assert quantity["is_potential_key"] is True
    dataset_node = next(
        node for node in result["nodes"] if node["type"] == "Dataset"
    )
    assert dataset_node["row_count"] == 1
    assert dataset_node["created_at"]
    node_ids = {node["id"] for node in result["nodes"]}
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in result["edges"]
    )


def test_graph_preserves_original_labels_and_sql_column_endpoints(tmp_path):
    customers_source = tmp_path / "customers.csv"
    customers_source.write_text(
        "Customer ID,Name\n1001,Alice\n1002,Bob\n1003,Cara\n",
        encoding="utf-8",
    )
    orders_source = tmp_path / "orders.csv"
    orders_source.write_text(
        "customer_id,amount\n1001,25\n1002,30\n1003,10\n",
        encoding="utf-8",
    )
    database = tmp_path / "graph.db"
    store_spreadsheet(customers_source, "customers.csv", database)
    store_spreadsheet(orders_source, "orders.csv", database)

    result = build_graph_from_database(database)

    customer_id_columns = [
        node
        for node in result["nodes"]
        if node["type"] == "Column"
        and node["sql_name"] == "customer_id"
    ]
    assert {node["label"] for node in customer_id_columns} == {
        "Customer ID",
        "customer_id",
    }
    assert all(node["id"].endswith(":customer_id") for node in customer_id_columns)

    relationships = [
        edge
        for edge in result["edges"]
        if edge["type"] == "CANDIDATE_MATCH"
    ]
    assert len(relationships) == 1
    node_ids = {node["id"] for node in result["nodes"]}
    assert relationships[0]["source"] in node_ids
    assert relationships[0]["target"] in node_ids


def test_graph_can_be_scoped_and_include_chunk_evidence(tmp_path):
    source = tmp_path / "scoped.csv"
    source.write_text("Product,Sales\nKeyboard,12\n", encoding="utf-8")
    database = tmp_path / "graph.db"
    stored = store_spreadsheet(
        source,
        "scoped.csv",
        database,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "text": "Product: Keyboard\nSales: 12",
                "metadata": {"source_rows": [1]},
            }
        ],
    )

    result = build_graph_from_database(
        database,
        dataset_ids=[stored["dataset_id"]],
        include_evidence=True,
    )

    evidence = [node for node in result["nodes"] if node["type"] == "Evidence"]
    assert len(evidence) == 1
    assert evidence[0]["chunk_id"] == "chunk-1"
    assert evidence[0]["dataset_id"] == stored["dataset_id"]
    assert any(
        edge["type"] == "HAS_EVIDENCE"
        and edge["target"] == evidence[0]["id"]
        for edge in result["edges"]
    )


def test_unchanged_complete_graph_is_not_rewritten(tmp_path, monkeypatch):
    source = tmp_path / "stable.csv"
    source.write_text("Product,Sales\nKeyboard,12\n", encoding="utf-8")
    database = tmp_path / "stable.db"
    stored = store_spreadsheet(
        source,
        "stable.csv",
        database,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "text": "Product: Keyboard\nSales: 12",
                "metadata": {"source_rows": [1]},
            }
        ],
    )
    original_replace = graph_service.replace_graph
    replacements = []

    def track_replace(*args, **kwargs):
        replacements.append(1)
        return original_replace(*args, **kwargs)

    monkeypatch.setattr(graph_service, "replace_graph", track_replace)

    first = build_graph_from_database(
        database,
        dataset_ids=[stored["dataset_id"]],
        include_evidence=True,
    )
    second = build_graph_from_database(
        database,
        dataset_ids=[stored["dataset_id"]],
        include_evidence=True,
    )

    assert first["nodes"] == second["nodes"]
    assert first["edges"] == second["edges"]
    assert len(replacements) == 1


def test_graph_rejects_missing_selected_dataset(tmp_path):
    source = tmp_path / "present.csv"
    source.write_text("value\n1\n", encoding="utf-8")
    database = tmp_path / "graph.db"
    store_spreadsheet(source, "present.csv", database)

    with pytest.raises(GraphServiceError, match="do not exist"):
        build_graph_from_database(
            database,
            dataset_ids=["missing"],
        )


def test_graph_rejects_stored_columns_missing_from_physical_schema(tmp_path):
    source = tmp_path / "profile.csv"
    source.write_text("Product Name,Quantity\nLaptop,2\n", encoding="utf-8")
    database = tmp_path / "graph.db"
    stored = store_spreadsheet(source, "profile.csv", database)

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE dataset_columns
            SET sql_name = 'missing_column'
            WHERE dataset_id = ? AND ordinal = 0
            """,
            (stored["dataset_id"],),
        )

    with pytest.raises(GraphServiceError, match="does not match the physical schema"):
        build_graph_from_database(database)


def test_scoped_graph_returns_the_persistent_database_graph(monkeypatch):
    persistent_graph = {
        "status": "success",
        "graph_store": "postgresql",
        "nodes": [{"id": "dataset:one", "dataset_id": "one"}],
        "edges": [],
        "candidate_relationships": [{"status": "candidate"}],
    }
    monkeypatch.setattr(
        graph_service,
        "build_graph_from_database",
        lambda *_args, **_kwargs: persistent_graph,
    )

    assert build_graph_for_scope("scope", ["dataset"]) == persistent_graph


@pytest.mark.parametrize("failed_operation", ["sync", "read"])
def test_scoped_graph_ignores_neo4j_failures(
    monkeypatch,
    caplog,
    failed_operation,
):
    credential_text = "password=must-not-be-logged"
    sqlite_graph = {
        "status": "success",
        "graph_store": "sqlite",
        "nodes": [{"id": "dataset:one", "label": "Sales"}],
        "edges": [{"id": "match:one", "type": "CANDIDATE_MATCH"}],
        "candidate_relationships": [{"status": "candidate", "confidence": 0.9}],
    }
    failure = neo4j_store.Neo4jStoreError(
        credential_text,
        code="NEO4J_UNAVAILABLE",
    )
    monkeypatch.setattr(
        graph_service,
        "build_graph_from_database",
        lambda *_args, **_kwargs: sqlite_graph,
    )
    monkeypatch.setattr(neo4j_store, "is_configured", lambda: True)
    if failed_operation == "sync":
        monkeypatch.setattr(
            neo4j_store,
            "sync_graph",
            lambda *_args: (_ for _ in ()).throw(failure),
        )
    else:
        monkeypatch.setattr(neo4j_store, "sync_graph", lambda *_args: True)
        monkeypatch.setattr(
            neo4j_store,
            "get_graph",
            lambda *_args: (_ for _ in ()).throw(failure),
        )

    with caplog.at_level("WARNING"):
        graph = build_graph_for_scope("scope", ["dataset"])

    assert graph["nodes"] == sqlite_graph["nodes"]
    assert graph["edges"] == sqlite_graph["edges"]
    assert graph["candidate_relationships"] == (
        sqlite_graph["candidate_relationships"]
    )
    assert graph["graph_store"] == "sqlite"
    assert "Neo4j graph unavailable" not in caplog.text
    assert credential_text not in caplog.text


def test_scoped_graph_always_uses_the_persistent_database(monkeypatch):
    sqlite_graph = {"nodes": [], "edges": [], "candidate_relationships": []}
    persisted_graph = {
        "scope_id": "scope",
        "nodes": [],
        "edges": [],
    }
    sync_calls = []
    monkeypatch.setattr(
        graph_service,
        "build_graph_from_database",
        lambda *_args, **_kwargs: sqlite_graph,
    )
    monkeypatch.setattr(neo4j_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        neo4j_store,
        "sync_graph",
        lambda *args: sync_calls.append(args),
    )
    monkeypatch.setattr(
        neo4j_store,
        "get_graph",
        lambda _scope: persisted_graph,
    )

    graph = build_graph_for_scope("scope", ["dataset"])

    assert sync_calls == []
    assert graph == sqlite_graph


def test_scoped_graph_preserves_validation_and_sqlite_build_errors(
    monkeypatch,
):
    failure = GraphServiceError("One or more selected datasets do not exist.")
    monkeypatch.setattr(
        graph_service,
        "build_graph_from_database",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        neo4j_store,
        "is_configured",
        lambda: pytest.fail("Neo4j must not be checked after SQLite failure"),
    )

    with pytest.raises(GraphServiceError) as raised:
        build_graph_for_scope("scope", ["invalid"])

    assert raised.value is failure


def test_scoped_empty_graph_does_not_require_neo4j(
    monkeypatch,
):
    empty_graph = {
        "status": "success",
        "nodes": [],
        "edges": [],
        "candidate_relationships": [],
    }
    sync_calls = []
    monkeypatch.setattr(
        graph_service,
        "build_graph_from_database",
        lambda *_args, **_kwargs: empty_graph,
    )
    monkeypatch.setattr(neo4j_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        neo4j_store,
        "sync_graph",
        lambda *args: sync_calls.append(args),
    )
    monkeypatch.setattr(
        neo4j_store,
        "get_graph",
        lambda scope: {"scope_id": scope, "nodes": [], "edges": []},
    )

    graph = build_graph_for_scope("empty-scope", [])

    assert sync_calls == []
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["candidate_relationships"] == []


def test_configured_neo4j_is_used_for_dataset_knowledge_graph(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "neo4j-graph.csv"
    source.write_text("Product,Sales\nLamp,10\n", encoding="utf-8")
    database = tmp_path / "neo4j-graph.db"
    stored = store_spreadsheet(source, "neo4j-graph.csv", database)
    persisted = {}
    writes = []

    def read_graph(dataset_ids):
        if not persisted:
            return {"nodes": [], "edges": [], "builds": []}
        graph = persisted["graph"]
        return {
            **graph,
            "builds": [
                {
                    "dataset_id": dataset_id,
                    "status": "complete",
                    "node_count": sum(
                        node.get("dataset_id") == dataset_id
                        for node in graph["nodes"]
                    ),
                    "edge_count": len(graph["edges"]),
                }
                for dataset_id in dataset_ids
            ],
        }

    def write_graph(graph, dataset_ids):
        writes.append(list(dataset_ids))
        persisted["graph"] = graph

    monkeypatch.setattr(graph_service.neo4j_store, "is_configured", lambda: True)
    monkeypatch.setattr(
        graph_service.neo4j_store,
        "get_dataset_graph",
        read_graph,
    )
    monkeypatch.setattr(
        graph_service.neo4j_store,
        "replace_dataset_graph",
        write_graph,
    )
    monkeypatch.setattr(
        graph_service,
        "replace_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "Neo4j configuration should not write the SQL graph store."
        ),
    )

    result = build_graph_from_database(database, [stored["dataset_id"]])

    assert result["graph_store"] == "neo4j"
    assert result["node_count"] > 0
    assert writes == [[stored["dataset_id"]]]
