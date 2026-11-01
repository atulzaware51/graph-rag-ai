import json
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from backend.app.core.database import (
    database_url_for_path,
    dispose_database_engines,
    get_database_engine,
    graph_edges,
    graph_evidence,
    graph_nodes,
    initialize_database,
)
from backend.app.services.graph_store import (
    GraphPersistenceError,
    delete_dataset_graph,
    get_persisted_graph,
    replace_graph,
)
from backend.app.services.graph_service import build_graph_from_database
from backend.app.services.dataset_store import delete_spreadsheet, store_spreadsheet


@pytest.fixture(autouse=True)
def clear_database_engines():
    dispose_database_engines()
    yield
    dispose_database_engines()


def graph_for(dataset_id, *, value="Revenue", include_evidence=True):
    dataset_node_id = f"dataset:{dataset_id}"
    column_node_id = f"column:{dataset_id}:revenue"
    nodes = [
        {
            "id": dataset_node_id,
            "type": "Dataset",
            "dataset_id": dataset_id,
            "label": f"{dataset_id}.csv",
            "filename": f"{dataset_id}.csv",
        },
        {
            "id": column_node_id,
            "type": "Column",
            "dataset_id": dataset_id,
            "label": value,
            "sql_name": "revenue",
            "data_type": "INTEGER",
        },
    ]
    edges = [
        {
            "id": f"contains:{dataset_id}:revenue",
            "source": dataset_node_id,
            "target": column_node_id,
            "type": "HAS_COLUMN",
        }
    ]
    if include_evidence:
        evidence_node_id = f"evidence:{dataset_id}:chunk-1"
        nodes.append(
            {
                "id": evidence_node_id,
                "type": "Evidence",
                "dataset_id": dataset_id,
                "label": "Evidence chunk 1",
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "text": "Revenue: 42",
                "metadata": {
                    "dataset_name": f"{dataset_id}.csv",
                    "sheet_name": "Sheet A",
                    "source_columns": ["Revenue"],
                    "source_rows": [7],
                },
            }
        )
        edges.append(
            {
                "id": f"evidence-link:{dataset_id}:chunk-1",
                "source": dataset_node_id,
                "target": evidence_node_id,
                "type": "HAS_EVIDENCE",
            }
        )
    return {"nodes": nodes, "edges": edges}


def test_persists_graph_nodes_edges_and_evidence_across_reconnection(tmp_path):
    url = database_url_for_path(tmp_path / "knowledge.db")
    graph = graph_for("sales")

    replace_graph(graph, ["sales"], database_url=url)
    dispose_database_engines()
    persisted = get_persisted_graph(dataset_ids=["sales"], database_url=url)

    assert {node["id"] for node in persisted["nodes"]} == {
        "dataset:sales",
        "column:sales:revenue",
        "evidence:sales:chunk-1",
    }
    evidence = next(
        node for node in persisted["nodes"] if node["type"] == "Evidence"
    )
    assert evidence["text"] == "Revenue: 42"
    assert evidence["metadata"]["source_rows"] == [7]
    assert len(persisted["edges"]) == 2
    assert persisted["builds"] == [
        {
            "dataset_id": "sales",
            "build_id": "graph-build:default:sales",
            "status": "complete",
            "node_count": 3,
            "edge_count": 2,
            "built_at": persisted["builds"][0]["built_at"],
        }
    ]

    engine = get_database_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            select(graph_evidence).where(
                graph_evidence.c.dataset_id == "sales"
            )
        ).mappings().one()
    assert row["source_file"] == "sales.csv"
    assert row["sheet_name"] == "Sheet A"
    assert json.loads(row["source_columns_json"]) == ["Revenue"]
    assert json.loads(row["source_rows_json"]) == [7]


def test_rebuild_is_idempotent_and_updates_stale_dataset_records(tmp_path):
    url = database_url_for_path(tmp_path / "knowledge.db")
    first = graph_for("first")
    second = graph_for("second")
    combined = {
        "nodes": first["nodes"] + second["nodes"],
        "edges": first["edges"] + second["edges"] + [
            {
                "id": "candidate:first:second",
                "source": "column:first:revenue",
                "target": "column:second:revenue",
                "type": "CANDIDATE_MATCH",
                "confidence": 0.9,
            }
        ],
    }

    replace_graph(combined, ["first", "second"], database_url=url)
    replace_graph(combined, ["first", "second"], database_url=url)
    initial = get_persisted_graph(database_url=url)
    assert len(initial["nodes"]) == 6
    assert len(initial["edges"]) == 5

    changed = graph_for("first", value="Net Revenue", include_evidence=False)
    replace_graph(changed, ["first"], database_url=url)
    updated = get_persisted_graph(database_url=url)

    assert "evidence:first:chunk-1" not in {
        node["id"] for node in updated["nodes"]
    }
    assert "dataset:second" in {node["id"] for node in updated["nodes"]}
    assert "column:first:revenue" in {node["id"] for node in updated["nodes"]}
    assert next(
        node for node in updated["nodes"]
        if node["id"] == "column:first:revenue"
    )["label"] == "Net Revenue"
    assert all(
        edge["type"] != "CANDIDATE_MATCH" for edge in updated["edges"]
    )
    assert len(updated["builds"]) == 2


def test_graph_records_are_isolated_by_workspace(tmp_path):
    url = database_url_for_path(tmp_path / "knowledge.db")
    replace_graph(graph_for("sales"), ["sales"], workspace_id="alpha", database_url=url)
    replace_graph(graph_for("sales"), ["sales"], workspace_id="beta", database_url=url)

    alpha = get_persisted_graph(workspace_id="alpha", database_url=url)
    beta = get_persisted_graph(workspace_id="beta", database_url=url)

    assert alpha["nodes"] == beta["nodes"]
    assert len(alpha["nodes"]) == 3
    assert len(beta["nodes"]) == 3


def test_failed_rebuild_rolls_back_old_graph_and_build_record(tmp_path):
    url = database_url_for_path(tmp_path / "knowledge.db")
    original = graph_for("sales")
    replace_graph(original, ["sales"], database_url=url)

    invalid = {
        "nodes": [
            {
                "id": "dataset:sales",
                "type": "Dataset",
                "dataset_id": "sales",
                "label": "changed.csv",
            },
            {
                "id": "evidence:sales:one",
                "type": "Evidence",
                "dataset_id": "sales",
                "label": "One",
                "chunk_id": "duplicate",
                "text": "First evidence",
                "metadata": {},
            },
            {
                "id": "evidence:sales:two",
                "type": "Evidence",
                "dataset_id": "sales",
                "label": "Two",
                "chunk_id": "duplicate",
                "text": "Second evidence",
                "metadata": {},
            },
        ],
        "edges": [],
    }
    with pytest.raises(GraphPersistenceError, match="persist"):
        replace_graph(invalid, ["sales"], database_url=url)

    restored = get_persisted_graph(dataset_ids=["sales"], database_url=url)
    assert {node["id"] for node in restored["nodes"]} == {
        node["id"] for node in original["nodes"]
    }
    assert restored["builds"][0]["status"] == "complete"


def test_dataset_deletion_clears_its_persistent_graph(tmp_path):
    source = tmp_path / "sales.csv"
    source.write_text("Revenue\n42\n", encoding="utf-8")
    database = tmp_path / "source.db"
    stored = store_spreadsheet(
        source,
        "sales.csv",
        database,
        chunks=[
            {
                "chunk_id": "chunk-1",
                "text": "Revenue: 42",
                "metadata": {"source_rows": [1]},
            }
        ],
    )
    build_graph_from_database(database)

    assert get_persisted_graph(database_url=database_url_for_path(database))[
        "nodes"
    ]
    assert delete_spreadsheet(stored["dataset_id"], database)

    assert get_persisted_graph(
        database_url=database_url_for_path(database)
    )["nodes"] == []


def test_dataset_graph_deletion_removes_attached_edges_and_build(tmp_path):
    url = database_url_for_path(tmp_path / "knowledge.db")
    first = graph_for("first", include_evidence=False)
    second = graph_for("second", include_evidence=False)
    graph = {
        "nodes": first["nodes"] + second["nodes"],
        "edges": first["edges"] + second["edges"] + [
            {
                "id": "candidate:first:second",
                "source": "column:first:revenue",
                "target": "column:second:revenue",
                "type": "CANDIDATE_MATCH",
            }
        ],
    }
    replace_graph(graph, ["first", "second"], database_url=url)

    delete_dataset_graph("first", database_url=url)
    remaining = get_persisted_graph(database_url=url)

    assert {node["dataset_id"] for node in remaining["nodes"]} == {"second"}
    assert all(edge["type"] == "HAS_COLUMN" for edge in remaining["edges"])
    assert [build["dataset_id"] for build in remaining["builds"]] == ["second"]


def test_legacy_conversation_graph_tables_are_renamed_without_data_loss(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY
            );
            CREATE TABLE graph_nodes (
                node_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE graph_edges (
                edge_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                source_id TEXT NOT NULL REFERENCES graph_nodes(node_id),
                target_id TEXT NOT NULL REFERENCES graph_nodes(node_id),
                relationship TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO conversations VALUES ('chat-1');
            INSERT INTO graph_nodes VALUES
                ('chat:chat-1', 'chat-1', 'Chat', 'Chat', '{}', 'now');
            INSERT INTO graph_edges VALUES
                ('edge-1', 'chat-1', 'chat:chat-1', 'chat:chat-1',
                 'SELF', '{}', 'now');
            """
        )

    initialize_database(database_url_for_path(database))

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT node_id FROM conversation_graph_nodes"
        ).fetchone() == ("chat:chat-1",)
        assert connection.execute(
            "SELECT edge_id FROM conversation_graph_edges"
        ).fetchone() == ("edge-1",)
        graph_node_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(graph_nodes)")
        }
    assert {"workspace_id", "dataset_id", "node_id"} <= graph_node_columns


def test_database_schema_compiles_for_postgresql():
    engine = get_database_engine(
        "postgresql://graph_user:graph_password@localhost/graphsheet"
    )
    assert engine.dialect.name == "postgresql"
    node_ddl = str(CreateTable(graph_nodes).compile(dialect=postgresql.dialect()))
    edge_ddl = str(CreateTable(graph_edges).compile(dialect=postgresql.dialect()))

    assert "PRIMARY KEY (workspace_id, dataset_id, node_id)" in node_ddl
    assert "FOREIGN KEY(workspace_id, source_dataset_id, source_id)" in edge_ddl
    assert "FOREIGN KEY(workspace_id, target_dataset_id, target_id)" in edge_ddl
