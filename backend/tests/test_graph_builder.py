
import pytest

from backend.app.services.graph_builder import (
    GraphBuilderError,
    build_knowledge_graph,
)


@pytest.fixture
def sample_datasets():
    return [
        {
            "dataset_id": "1",
            "filename": "students.csv",
            "table_name": "dataset_1",
            "columns": [
                {
                    "name": "student_id",
                    "sql_name": "student_id",
                    "data_type": "INTEGER",
                },
                {
                    "name": "name",
                    "sql_name": "name",
                    "data_type": "TEXT",
                },
            ],
        },
        {
            "dataset_id": "2",
            "filename": "marks.csv",
            "table_name": "dataset_2",
            "columns": [
                {
                    "name": "student_id",
                    "sql_name": "student_id",
                    "data_type": "INTEGER",
                },
                {
                    "name": "marks",
                    "sql_name": "marks",
                    "data_type": "REAL",
                },
            ],
        },
    ]


@pytest.fixture
def sample_relationship():
    return [
        {
            "left": {
                "dataset_id": "1",
                "column": "student_id",
            },
            "right": {
                "dataset_id": "2",
                "column": "student_id",
            },
            "status": "candidate",
            "confidence": 0.95,
        }
    ]


def test_builds_dataset_and_column_nodes(sample_datasets):
    graph = build_knowledge_graph(sample_datasets, [])

    assert graph["node_count"] == 6
    assert len(graph["nodes"]) == 6

    dataset_nodes = [
        node for node in graph["nodes"]
        if node["type"] == "Dataset"
    ]
    column_nodes = [
        node for node in graph["nodes"]
        if node["type"] == "Column"
    ]

    assert len(dataset_nodes) == 2
    assert len(column_nodes) == 4


def test_creates_has_column_edges(sample_datasets):
    graph = build_knowledge_graph(sample_datasets, [])

    edges = [
        edge for edge in graph["edges"]
        if edge["type"] == "HAS_COLUMN"
    ]

    assert len(edges) == 4


def test_creates_candidate_relationship_edge(
    sample_datasets,
    sample_relationship,
):
    graph = build_knowledge_graph(
        sample_datasets,
        sample_relationship,
    )

    matches = [
        edge for edge in graph["edges"]
        if edge["type"] == "CANDIDATE_MATCH"
    ]

    assert len(matches) == 1
    assert matches[0]["status"] == "candidate"
    assert matches[0]["confidence"] == 0.95
    assert matches[0]["source"] == "column:1:student_id"
    assert matches[0]["target"] == "column:2:student_id"
    assert matches[0]["left_dataset_id"] == "1"
    assert matches[0]["right_dataset_id"] == "2"


def test_creates_candidate_edge_for_customer_order_relationship():
    datasets = [
        {
            "dataset_id": "customers",
            "filename": "customers.csv",
            "table_name": "dataset_customers",
            "columns": [
                {
                    "name": "customer_id",
                    "sql_name": "customer_id",
                    "data_type": "INTEGER",
                },
                {
                    "name": "customer_name",
                    "sql_name": "customer_name",
                    "data_type": "TEXT",
                },
            ],
        },
        {
            "dataset_id": "orders",
            "filename": "orders.csv",
            "table_name": "dataset_orders",
            "columns": [
                {
                    "name": "order_id",
                    "sql_name": "order_id",
                    "data_type": "INTEGER",
                },
                {
                    "name": "customer_id",
                    "sql_name": "customer_id",
                    "data_type": "INTEGER",
                },
                {
                    "name": "amount",
                    "sql_name": "amount",
                    "data_type": "REAL",
                },
            ],
        },
    ]
    relationships = [
        {
            "left": {
                "dataset_id": "customers",
                "column": "customer_id",
            },
            "right": {
                "dataset_id": "orders",
                "column": "customer_id",
            },
            "status": "candidate",
            "confidence": 0.96,
            "shared_value_count": 4,
            "name_similarity": 1.0,
            "overlap_ratio": 1.0,
            "evidence": "Matching customer IDs across both tables.",
        }
    ]

    graph = build_knowledge_graph(datasets, relationships)
    matches = [
        edge for edge in graph["edges"]
        if edge["type"] == "CANDIDATE_MATCH"
    ]

    assert len(matches) == 1
    assert matches[0]["source"] == "column:customers:customer_id"
    assert matches[0]["target"] == "column:orders:customer_id"
    assert matches[0]["shared_value_count"] == 4
    assert matches[0]["confidence"] == 0.96


def test_creates_approved_relationship_edge(
    sample_datasets,
    sample_relationship,
):
    sample_relationship[0]["status"] = "approved"

    graph = build_knowledge_graph(
        sample_datasets,
        sample_relationship,
    )

    matches = [
        edge for edge in graph["edges"]
        if edge["type"] == "RELATED_TO"
    ]

    assert len(matches) == 1
    assert matches[0]["status"] == "approved"


def test_ignores_relationship_with_unknown_column(
    sample_datasets,
):
    relationships = [
        {
            "left": {
                "dataset_id": "1",
                "column": "missing_column",
            },
            "right": {
                "dataset_id": "2",
                "column": "student_id",
            },
            "status": "candidate",
            "confidence": 0.80,
        }
    ]

    graph = build_knowledge_graph(sample_datasets, relationships)

    relationship_edges = [
        edge for edge in graph["edges"]
        if edge["type"] in ("CANDIDATE_MATCH", "RELATED_TO")
    ]

    assert relationship_edges == []


def test_rejects_duplicate_dataset_ids(sample_datasets):
    sample_datasets.append(sample_datasets[0].copy())

    with pytest.raises(GraphBuilderError, match="Duplicate dataset ID"):
        build_knowledge_graph(sample_datasets, [])


def test_rejects_invalid_confidence(
    sample_datasets,
    sample_relationship,
):
    sample_relationship[0]["confidence"] = 1.5

    with pytest.raises(
        GraphBuilderError,
        match="confidence must be between 0 and 1",
    ):
        build_knowledge_graph(sample_datasets, sample_relationship)


def test_rejects_duplicate_column_names(sample_datasets):
    sample_datasets[0]["columns"].append(
        {
            "name": "student_id",
            "sql_name": "student_id",
            "data_type": "INTEGER",
        }
    )

    with pytest.raises(GraphBuilderError, match="Duplicate column name"):
        build_knowledge_graph(sample_datasets, [])
