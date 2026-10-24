
import sqlite3

import pytest

from backend.app.services.graph_service import (
    GraphServiceError,
    build_graph_from_database,
)
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
