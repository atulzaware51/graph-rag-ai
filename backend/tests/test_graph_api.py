from fastapi.testclient import TestClient

from backend.app.api import graph as graph_api
from backend.app.main import app


client = TestClient(app)


def test_graph_limit_keeps_edge_endpoints_in_response(monkeypatch):
    monkeypatch.setattr(
        graph_api,
        "build_graph_from_database",
        lambda: {
            "status": "success",
            "node_count": 3,
            "edge_count": 2,
            "nodes": [
                {"id": "dataset:1"},
                {"id": "column:1:name"},
                {"id": "dataset:2"},
            ],
            "edges": [
                {
                    "id": "contains:1:name",
                    "source": "dataset:1",
                    "target": "column:1:name",
                },
                {
                    "id": "relationship:1",
                    "source": "column:1:name",
                    "target": "dataset:2",
                },
            ],
        },
    )

    response = client.get("/graph/?limit=2")

    assert response.status_code == 200
    body = response.json()
    node_ids = {node["id"] for node in body["nodes"]}
    assert body["returned_node_count"] == 2
    assert body["returned_edge_count"] == 1
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in body["edges"]
    )


def test_graph_api_includes_candidate_edges_with_schema_fields(monkeypatch):
    monkeypatch.setattr(
        graph_api,
        "build_graph_from_database",
        lambda: {
            "status": "success",
            "dataset_count": 2,
            "relationship_candidate_count": 1,
            "node_count": 6,
            "edge_count": 5,
            "nodes": [
                {
                    "id": "dataset:customers",
                    "type": "Dataset",
                    "label": "customers.csv",
                    "dataset_id": "customers",
                    "table_name": "dataset_customers",
                },
                {
                    "id": "column:customers:customer_id",
                    "type": "Column",
                    "label": "customer_id",
                    "dataset_id": "customers",
                    "data_type": "INTEGER",
                    "sql_name": "customer_id",
                },
                {
                    "id": "dataset:orders",
                    "type": "Dataset",
                    "label": "orders.csv",
                    "dataset_id": "orders",
                    "table_name": "dataset_orders",
                },
                {
                    "id": "column:orders:customer_id",
                    "type": "Column",
                    "label": "customer_id",
                    "dataset_id": "orders",
                    "data_type": "INTEGER",
                    "sql_name": "customer_id",
                },
                {
                    "id": "column:orders:amount",
                    "type": "Column",
                    "label": "amount",
                    "dataset_id": "orders",
                    "data_type": "REAL",
                    "sql_name": "amount",
                },
            ],
            "edges": [
                {
                    "id": "contains:customers:customer_id",
                    "source": "dataset:customers",
                    "target": "column:customers:customer_id",
                    "type": "HAS_COLUMN",
                },
                {
                    "id": "contains:orders:customer_id",
                    "source": "dataset:orders",
                    "target": "column:orders:customer_id",
                    "type": "HAS_COLUMN",
                },
                {
                    "id": "contains:orders:amount",
                    "source": "dataset:orders",
                    "target": "column:orders:amount",
                    "type": "HAS_COLUMN",
                },
                {
                    "id": "relationship:1",
                    "source": "column:customers:customer_id",
                    "target": "column:orders:customer_id",
                    "type": "CANDIDATE_MATCH",
                    "status": "candidate",
                    "confidence": 0.96,
                    "shared_value_count": 4,
                    "name_similarity": 1.0,
                    "overlap_ratio": 1.0,
                    "evidence": "Matching customer IDs across both tables.",
                    "left_dataset_id": "customers",
                    "right_dataset_id": "orders",
                    "left_column": "customer_id",
                    "right_column": "customer_id",
                },
            ],
        },
    )

    response = client.get("/graph/?limit=500")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["dataset_count"] == 2
    assert body["relationship_candidate_count"] == 1
    assert body["returned_node_count"] == 5
    assert body["returned_edge_count"] == 4

    has_column_edges = [
        edge for edge in body["edges"] if edge["type"] == "HAS_COLUMN"
    ]
    candidate_edges = [
        edge for edge in body["edges"] if edge["type"] == "CANDIDATE_MATCH"
    ]
    assert len(has_column_edges) == 3
    assert len(candidate_edges) == 1
    assert candidate_edges[0]["status"] == "candidate"
    assert candidate_edges[0]["confidence"] == 0.96
