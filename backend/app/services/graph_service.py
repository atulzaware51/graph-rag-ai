
"""Integrate stored datasets and inferred relationships into a graph."""

import json
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
from backend.app.core.database import (
    get_database_engine,
    graph_database_url_for_source,
)
from backend.app.services.graph_store import (
    GraphPersistenceError,
    get_persisted_graph,
    replace_graph,
)
from backend.app.services import neo4j_store
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


def _graph_matches_persisted(
    graph: dict[str, Any],
    persisted_graph: dict[str, Any],
    dataset_ids: list[str],
) -> bool:
    """Avoid rewriting graph storage when the selected graph is unchanged."""
    builds = persisted_graph.get("builds", [])
    if len(builds) != len(dataset_ids) or any(
        not isinstance(build, dict)
        or build.get("status") != "complete"
        or build.get("dataset_id") not in dataset_ids
        for build in builds
    ):
        return False

    def signatures(items: Any, identifier: str) -> dict[str, str] | None:
        """Create order-independent fingerprints for nodes or edges by ID."""
        if not isinstance(items, list):
            return None
        result = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get(identifier), str):
                return None
            result[item[identifier]] = json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        return result

    expected_nodes = signatures(graph.get("nodes"), "id")
    stored_nodes = signatures(persisted_graph.get("nodes"), "id")
    expected_edges = signatures(graph.get("edges"), "id")
    stored_edges = signatures(persisted_graph.get("edges"), "id")
    return (
        expected_nodes is not None
        and expected_nodes == stored_nodes
        and expected_edges is not None
        and expected_edges == stored_edges
    )


class GraphServiceError(Exception):
    """Raised when the knowledge graph cannot be assembled."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Carry a stable API code and retry delay with graph build failures."""
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds


def build_graph_for_scope(
    _scope_id: str,
    dataset_ids: list[str],
    *,
    include_evidence: bool = False,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return a selected graph after rebuilding its persistent database records."""
    return build_graph_from_database(
        database_path,
        dataset_ids=dataset_ids,
        include_evidence=include_evidence,
    )


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
        selected_dataset_ids = [
            dataset["dataset_id"] for dataset in graph_datasets
        ]
        use_neo4j = bool(selected_dataset_ids) and neo4j_store.is_configured()
        if use_neo4j:
            try:
                persisted_graph = neo4j_store.get_dataset_graph(
                    dataset_ids=selected_dataset_ids,
                )
                if not _graph_matches_persisted(
                    graph,
                    persisted_graph,
                    selected_dataset_ids,
                ):
                    neo4j_store.replace_dataset_graph(
                        graph,
                        selected_dataset_ids,
                    )
                    persisted_graph = neo4j_store.get_dataset_graph(
                        dataset_ids=selected_dataset_ids,
                    )
            except neo4j_store.Neo4jStoreError as exc:
                logger.warning(
                    "Neo4j knowledge graph unavailable (%s); using the SQL graph store.",
                    exc.code,
                )
                use_neo4j = False

        if not use_neo4j:
            persisted_graph = get_persisted_graph(
                dataset_ids=selected_dataset_ids,
                database_url=graph_database_url_for_source(database),
            )
            if not _graph_matches_persisted(
                graph,
                persisted_graph,
                selected_dataset_ids,
            ):
                replace_graph(
                    graph,
                    selected_dataset_ids,
                    database_url=graph_database_url_for_source(database),
                )
                persisted_graph = get_persisted_graph(
                    dataset_ids=selected_dataset_ids,
                    database_url=graph_database_url_for_source(database),
                )
        graph_nodes = persisted_graph["nodes"]
        graph_edges = persisted_graph["edges"]
        if not include_evidence:
            visible_node_ids = {
                node["id"]
                for node in graph_nodes
                if node["type"] != "Evidence"
            }
            graph_nodes = [
                node for node in graph_nodes
                if node["id"] in visible_node_ids
            ]
            graph_edges = [
                edge for edge in graph_edges
                if edge["source"] in visible_node_ids
                and edge["target"] in visible_node_ids
            ]

        return {
            "status": "success",
            "dataset_count": len(graph_datasets),
            "relationship_candidate_count": len(relationships),
            "candidate_relationships": relationships,
            "node_count": len(graph_nodes),
            "edge_count": len(graph_edges),
            "nodes": graph_nodes,
            "edges": graph_edges,
            "graph_store": (
                "neo4j"
                if use_neo4j
                else get_database_engine(
                    graph_database_url_for_source(database)
                ).dialect.name
            ),
        }

    except (
        DatasetReaderError,
        DatasetNotFoundError,
        DatasetStorageError,
        SchemaProfilerError,
        RelationshipInferenceError,
        GraphBuilderError,
        GraphPersistenceError,
        neo4j_store.Neo4jStoreError,
    ) as exc:
        raise GraphServiceError(
            f"Could not build knowledge graph: {exc}"
        ) from exc
