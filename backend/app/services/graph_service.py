
"""Integrate stored datasets and inferred relationships into a graph."""

import logging
from pathlib import Path
from typing import Any

from backend.app.services.dataset_reader import (
    DatasetReaderError,
    DatasetNotFoundError,
    get_dataset_details,
    list_datasets,
)
from backend.app.services.dataset_store import (
    DatasetStorageError,
    get_dataset_chunks,
)
from backend.app.services.graph_builder import (
    GraphBuilderError,
    build_knowledge_graph,
)
from backend.app.services.relationship_inference import (
    RelationshipInferenceError,
    infer_relationships,
)
from backend.app.services.schema_profiler import (
    SchemaProfilerError,
    profile_dataset,
)


DEFAULT_DATABASE_PATH = Path("data/graphsheet.db")
logger = logging.getLogger(__name__)


class GraphServiceError(Exception):
    """Raised when the knowledge graph cannot be assembled."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


def build_graph_for_scope(
    scope_id: str,
    dataset_ids: list[str],
    *,
    include_evidence: bool = False,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Build from SQLite, using Neo4j as an optional graph projection."""
    graph = build_graph_from_database(
        database_path,
        dataset_ids=dataset_ids,
        include_evidence=include_evidence,
    )
    from backend.app.services.neo4j_store import (
        Neo4jStoreError,
        get_graph,
        is_configured,
        sync_graph,
    )

    if not is_configured():
        return graph

    try:
        sync_graph(scope_id, graph)
        persisted_graph = get_graph(scope_id)
    except Neo4jStoreError as exc:
        logger.warning(
            "Neo4j graph unavailable; serving SQLite graph (%s).",
            exc.code,
        )
        return {**graph, "graph_store": "sqlite"}

    node_by_id = {
        node["id"]: node
        for node in persisted_graph.get("nodes", [])
        if node.get("type") == "Column"
    }
    dataset_by_id = {
        node.get("dataset_id"): node
        for node in persisted_graph.get("nodes", [])
        if node.get("type") == "Dataset"
    }
    persisted_graph["candidate_relationships"] = [
        {
            "left": {
                "dataset_id": left["dataset_id"],
                "column": left["sql_name"],
                "filename": dataset_by_id.get(
                    left["dataset_id"], {}
                ).get("filename"),
                "table_name": dataset_by_id.get(
                    left["dataset_id"], {}
                ).get("table_name"),
            },
            "right": {
                "dataset_id": right["dataset_id"],
                "column": right["sql_name"],
                "filename": dataset_by_id.get(
                    right["dataset_id"], {}
                ).get("filename"),
                "table_name": dataset_by_id.get(
                    right["dataset_id"], {}
                ).get("table_name"),
            },
            **{
                key: value
                for key, value in edge.items()
                if key not in {"id", "source", "target", "type"}
            },
        }
        for edge in persisted_graph.get("edges", [])
        if edge.get("type") in {"CANDIDATE_MATCH", "RELATED_TO"}
        and (left := node_by_id.get(edge.get("source"))) is not None
        and (right := node_by_id.get(edge.get("target"))) is not None
    ]
    return {**persisted_graph, "graph_store": "neo4j"}


def build_graph_from_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    dataset_ids: list[str] | None = None,
    *,
    include_evidence: bool = False,
) -> dict[str, Any]:
    """Build a graph from selected stored schemas, profiles, and inferred links."""

    database = Path(database_path)

    try:
        stored_datasets = list_datasets(database)
        if dataset_ids is not None:
            if not isinstance(dataset_ids, list):
                raise GraphServiceError("Dataset IDs must be supplied as a list.")
            if any(
                not isinstance(dataset_id, str) or not dataset_id.strip()
                for dataset_id in dataset_ids
            ):
                raise GraphServiceError(
                    "Dataset IDs must be non-empty strings."
                )
            selected_ids = set(dataset_ids)
            stored_datasets = [
                dataset
                for dataset in stored_datasets
                if dataset["dataset_id"] in selected_ids
            ]
            missing_ids = selected_ids - {
                dataset["dataset_id"] for dataset in stored_datasets
            }
            if missing_ids:
                raise GraphServiceError(
                    "One or more selected datasets do not exist."
                )

        graph_datasets: list[dict[str, Any]] = []

        for dataset in stored_datasets:
            dataset_id = dataset["dataset_id"]
            schema = profile_dataset(
                dataset_id,
                database_path=database,
            )
            stored_dataset = get_dataset_details(
                dataset_id,
                database_path=database,
            )

            columns = schema.get("columns", [])
            stored_columns = stored_dataset.get("columns", [])
            stored_columns_by_sql_name = {
                column.get("sql_name"): column
                for column in stored_columns
                if isinstance(column.get("sql_name"), str)
            }

            if (
                len(stored_columns_by_sql_name) != len(stored_columns)
                or set(stored_columns_by_sql_name)
                != {
                    column.get("name")
                    for column in columns
                    if isinstance(column.get("name"), str)
                }
            ):
                raise GraphServiceError(
                    "Stored column metadata does not match the physical "
                    f"schema for dataset {dataset_id!r}."
                )

            graph_datasets.append(
                {
                    "dataset_id": dataset_id,
                    "filename": dataset.get(
                        "original_filename",
                        dataset.get("filename", ""),
                    ),
                    "table_name": dataset["table_name"],
                    "created_at": dataset.get("created_at"),
                    "row_count": dataset.get("row_count"),
                    "column_count": dataset.get("column_count"),
                    "columns": [
                        {
                            "name": stored_columns_by_sql_name[
                                column["name"]
                            ]["original_name"],
                            "sql_name": column["name"],
                            "data_type": column.get(
                                "data_type",
                                column.get("sqlite_type", "UNKNOWN"),
                            ),
                            "null_count": column.get("null_count"),
                            "unique_count": column.get("unique_count"),
                            "sample_values": column.get("sample_values", []),
                            "is_potential_key": column.get(
                                "is_potential_key",
                                False,
                            ),
                        }
                        for column in columns
                    ],
                }
            )

        inference = infer_relationships(
            database,
            dataset_ids=dataset_ids,
        )
        relationships = inference.get("relationships", [])

        evidence: list[dict[str, Any]] = []
        if include_evidence:
            for dataset in graph_datasets:
                evidence.extend(
                    get_dataset_chunks(
                        dataset["dataset_id"],
                        database_path=database,
                    )
                )

        graph = build_knowledge_graph(
            datasets=graph_datasets,
            relationships=relationships,
            evidence=evidence,
        )

        return {
            "status": "success",
            "dataset_count": len(graph_datasets),
            "relationship_candidate_count": len(relationships),
            "candidate_relationships": relationships,
            **graph,
        }

    except (
        DatasetReaderError,
        DatasetNotFoundError,
        DatasetStorageError,
        SchemaProfilerError,
        RelationshipInferenceError,
        GraphBuilderError,
    ) as exc:
        raise GraphServiceError(
            f"Could not build knowledge graph: {exc}"
        ) from exc
