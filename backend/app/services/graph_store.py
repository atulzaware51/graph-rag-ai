"""Persistent, database-backed storage for dataset knowledge graphs."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.core.database import (
    get_database_engine,
    graph_builds,
    graph_edges,
    graph_evidence,
    graph_nodes,
    initialize_database,
)


logger = logging.getLogger(__name__)
DEFAULT_WORKSPACE_ID = "default"


class GraphPersistenceError(RuntimeError):
    """Raised when a persistent graph operation fails."""


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        raise GraphPersistenceError(
            "Graph properties could not be serialized."
        ) from exc


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _update_dataset_graph_status(
    dataset_ids: list[str],
    *,
    status: str,
    database_url: str | None = None,
    error: str | None = None,
) -> None:
    """Keep the dataset metadata table aligned with the latest graph build state."""
    if not dataset_ids or not database_url:
        return
    normalized_url = database_url.strip()
    if not normalized_url.startswith("sqlite"):
        return
    if normalized_url.startswith("sqlite:///:memory:"):
        return
    if normalized_url.startswith("sqlite:///"):
        database = normalized_url[len("sqlite:///") :]
    elif normalized_url.startswith("sqlite://"):
        database = normalized_url[len("sqlite://") :]
    else:
        return
    if database.startswith("file:"):
        database = database[len("file:") :]
    try:
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            for dataset_id in dataset_ids:
                connection.execute(
                    """
                    UPDATE datasets
                    SET graph_build_status = ?,
                        graph_updated_at = ?,
                        graph_error = ?
                    WHERE dataset_id = ?
                    """,
                    (
                        status,
                        datetime.now(timezone.utc).isoformat(),
                        error,
                        dataset_id,
                    ),
                )
    except sqlite3.Error:
        logger.debug("Dataset graph status update skipped for SQLite database.", exc_info=True)


def _required_string(
    value: Any,
    field: str,
    *,
    maximum_length: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GraphPersistenceError(f"Graph {field} must be a non-empty string.")
    normalized = value.strip()
    if maximum_length is not None and len(normalized) > maximum_length:
        raise GraphPersistenceError(
            f"Graph {field} exceeds the supported length."
        )
    return normalized


def replace_graph(
    graph: dict[str, Any],
    dataset_ids: list[str],
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    database_url: str | None = None,
) -> None:
    """Atomically replace selected dataset graph records and build markers."""
    workspace_id = _required_string(
        workspace_id,
        "workspace_id",
        maximum_length=80,
    )
    if not isinstance(dataset_ids, list) or any(
        not isinstance(dataset_id, str)
        or not dataset_id.strip()
        or len(dataset_id.strip()) > 80
        for dataset_id in dataset_ids
    ):
        raise GraphPersistenceError(
            "Graph dataset IDs must be a list of non-empty strings."
        )
    selected_ids = list(dict.fromkeys(item.strip() for item in dataset_ids))
    if not isinstance(graph, dict):
        raise GraphPersistenceError("Graph data must be an object.")
    if not selected_ids:
        initialize_database(database_url)
        return

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise GraphPersistenceError("Graph nodes and edges must be lists.")

    prepared_nodes: list[dict[str, Any]] = []
    prepared_evidence: list[dict[str, Any]] = []
    node_dataset_by_id: dict[str, str] = {}
    dataset_labels: dict[str, str] = {}
    node_counts = {dataset_id: 0 for dataset_id in selected_ids}

    for node in nodes:
        if not isinstance(node, dict):
            raise GraphPersistenceError("Each graph node must be an object.")
        node_id = _required_string(
            node.get("id"),
            "node ID",
            maximum_length=255,
        )
        dataset_id = _required_string(
            node.get("dataset_id"),
            "node dataset_id",
            maximum_length=80,
        )
        node_type = _required_string(node.get("type"), "node type")
        label = _required_string(node.get("label", node_id), "node label")
        if dataset_id not in selected_ids:
            raise GraphPersistenceError(
                "A graph node is outside the selected dataset scope."
            )
        if node_id in node_dataset_by_id:
            raise GraphPersistenceError(f"Duplicate graph node ID: {node_id}")
        node_dataset_by_id[node_id] = dataset_id
        node_counts[dataset_id] += 1
        if node_type == "Dataset":
            dataset_labels[dataset_id] = str(
                node.get("filename") or node.get("label") or dataset_id
            )

        properties = {
            key: value
            for key, value in node.items()
            if key not in {"id", "dataset_id", "type", "label"}
        }
        prepared_node = {
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "node_id": node_id,
            "node_type": node_type,
            "label": label,
            "properties_json": _json(
                {
                    key: value
                    for key, value in properties.items()
                    if node_type != "Evidence" or key not in {"text", "metadata"}
                }
            ),
        }
        prepared_nodes.append(prepared_node)

        if node_type == "Evidence":
            metadata = node.get("metadata", {})
            if not isinstance(metadata, dict):
                raise GraphPersistenceError(
                    "Evidence metadata must be an object."
                )
            text = node.get("text")
            if not isinstance(text, str) or not text.strip():
                raise GraphPersistenceError(
                    "Evidence text must be a non-empty string."
                )
            evidence_id = _required_string(
                node.get("chunk_id", node_id),
                "evidence ID",
                maximum_length=255,
            )
            prepared_evidence.append(
                {
                    "workspace_id": workspace_id,
                    "dataset_id": dataset_id,
                    "evidence_id": evidence_id,
                    "node_id": node_id,
                    "source_file": (
                        metadata.get("source_file")
                        or metadata.get("dataset_name")
                        or dataset_labels.get(dataset_id)
                    ),
                    "sheet_name": (
                        metadata.get("sheet_name")
                        or metadata.get("source_sheet")
                        or metadata.get("sheet")
                    ),
                    "source_columns_json": _json(
                        _string_list(
                            metadata.get("source_columns")
                            or metadata.get("columns")
                        )
                    ),
                    "source_rows_json": _json(
                        metadata.get("source_rows")
                        or metadata.get("row_numbers")
                        or []
                    ),
                    "evidence_text": text,
                    "metadata_json": _json(metadata),
                }
            )

    prepared_edges: list[dict[str, Any]] = []
    edge_counts = {dataset_id: 0 for dataset_id in selected_ids}
    for edge in edges:
        if not isinstance(edge, dict):
            raise GraphPersistenceError("Each graph edge must be an object.")
        edge_id = _required_string(
            edge.get("id"),
            "edge ID",
            maximum_length=255,
        )
        source_id = _required_string(
            edge.get("source"),
            "edge source",
            maximum_length=255,
        )
        target_id = _required_string(
            edge.get("target"),
            "edge target",
            maximum_length=255,
        )
        relationship = _required_string(edge.get("type"), "relationship type")
        source_dataset_id = node_dataset_by_id.get(source_id)
        target_dataset_id = node_dataset_by_id.get(target_id)
        if source_dataset_id is None or target_dataset_id is None:
            raise GraphPersistenceError(
                f"Graph edge {edge_id!r} references a node outside the graph."
            )
        prepared_edges.append(
            {
                "workspace_id": workspace_id,
                "dataset_id": source_dataset_id,
                "edge_id": edge_id,
                "source_dataset_id": source_dataset_id,
                "source_id": source_id,
                "target_dataset_id": target_dataset_id,
                "target_id": target_id,
                "relationship": relationship,
                "properties_json": _json(
                    {
                        key: value
                        for key, value in edge.items()
                        if key not in {"id", "source", "target", "type"}
                    }
                ),
            }
        )
        edge_counts[source_dataset_id] += 1

    node_ids = set(node_dataset_by_id)
    if len(node_ids) != len(prepared_nodes):
        raise GraphPersistenceError("Graph node IDs must be unique.")

    now = datetime.now(timezone.utc).isoformat()
    build_rows = [
        {
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "build_id": f"graph-build:{workspace_id}:{dataset_id}",
            "status": "complete",
            "node_count": node_counts[dataset_id],
            "edge_count": edge_counts[dataset_id],
            "built_at": now,
        }
        for dataset_id in selected_ids
    ]

    try:
        initialize_database(database_url)
        engine = get_database_engine(database_url)
        with engine.begin() as connection:
            selected = graph_nodes.c.dataset_id.in_(selected_ids)
            affected_edges = or_(
                graph_edges.c.source_dataset_id.in_(selected_ids),
                graph_edges.c.target_dataset_id.in_(selected_ids),
            )
            connection.execute(
                delete(graph_edges).where(
                    and_(
                        graph_edges.c.workspace_id == workspace_id,
                        affected_edges,
                    )
                )
            )
            connection.execute(
                delete(graph_evidence).where(
                    and_(
                        graph_evidence.c.workspace_id == workspace_id,
                        graph_evidence.c.dataset_id.in_(selected_ids),
                    )
                )
            )
            connection.execute(
                delete(graph_nodes).where(
                    and_(
                        graph_nodes.c.workspace_id == workspace_id,
                        selected,
                    )
                )
            )
            connection.execute(
                delete(graph_builds).where(
                    and_(
                        graph_builds.c.workspace_id == workspace_id,
                        graph_builds.c.dataset_id.in_(selected_ids),
                    )
                )
            )
            if prepared_nodes:
                connection.execute(graph_nodes.insert(), prepared_nodes)
            if prepared_evidence:
                connection.execute(graph_evidence.insert(), prepared_evidence)
            if prepared_edges:
                connection.execute(graph_edges.insert(), prepared_edges)
            connection.execute(graph_builds.insert(), build_rows)
            _update_dataset_graph_status(
                selected_ids,
                status="complete",
                database_url=database_url,
            )
    except GraphPersistenceError:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Persistent graph replacement failed.")
        _update_dataset_graph_status(
            selected_ids,
            status="failed",
            database_url=database_url,
            error=str(exc),
        )
        raise GraphPersistenceError(
            "Could not persist the knowledge graph transaction."
        ) from exc


def get_persisted_graph(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    dataset_ids: list[str] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Read graph nodes, directed edges, evidence provenance, and build counts."""
    workspace_id = _required_string(
        workspace_id,
        "workspace_id",
        maximum_length=80,
    )
    if dataset_ids is not None and (
        not isinstance(dataset_ids, list)
        or any(        not isinstance(item, str)
        or not item.strip()
        or len(item.strip()) > 80
        for item in dataset_ids)
    ):
        raise GraphPersistenceError(
            "Graph dataset IDs must be a list of non-empty strings."
        )
    selected_ids = (
        list(dict.fromkeys(item.strip() for item in dataset_ids))
        if dataset_ids is not None
        else None
    )
    try:
        initialize_database(database_url)
        engine = get_database_engine(database_url)
        with engine.connect() as connection:
            node_query = select(graph_nodes).where(
                graph_nodes.c.workspace_id == workspace_id
            )
            edge_query = select(graph_edges).where(
                graph_edges.c.workspace_id == workspace_id
            )
            evidence_query = select(graph_evidence).where(
                graph_evidence.c.workspace_id == workspace_id
            )
            build_query = select(graph_builds).where(
                graph_builds.c.workspace_id == workspace_id
            )
            if selected_ids is not None:
                node_query = node_query.where(
                    graph_nodes.c.dataset_id.in_(selected_ids)
                )
                edge_query = edge_query.where(
                    graph_edges.c.source_dataset_id.in_(selected_ids),
                    graph_edges.c.target_dataset_id.in_(selected_ids),
                )
                evidence_query = evidence_query.where(
                    graph_evidence.c.dataset_id.in_(selected_ids)
                )
                build_query = build_query.where(
                    graph_builds.c.dataset_id.in_(selected_ids)
                )
            node_rows = connection.execute(node_query).mappings().all()
            edge_rows = connection.execute(edge_query).mappings().all()
            evidence_rows = connection.execute(evidence_query).mappings().all()
            build_rows = connection.execute(build_query).mappings().all()
    except SQLAlchemyError as exc:
        logger.exception("Persistent graph read failed.")
        raise GraphPersistenceError(
            "Could not read the persistent knowledge graph."
        ) from exc

    evidence_by_node = {
        row["node_id"]: row
        for row in evidence_rows
    }
    output_nodes = []
    for row in node_rows:
        properties = json.loads(row["properties_json"])
        node = {
            **properties,
            "id": row["node_id"],
            "type": row["node_type"],
            "label": row["label"],
            "dataset_id": row["dataset_id"],
        }
        evidence = evidence_by_node.get(row["node_id"])
        if evidence is not None:
            node["text"] = evidence["evidence_text"]
            node["metadata"] = json.loads(evidence["metadata_json"])
        output_nodes.append(node)

    output_edges = [
        {
            **json.loads(row["properties_json"]),
            "id": row["edge_id"],
            "source": row["source_id"],
            "target": row["target_id"],
            "type": row["relationship"],
        }
        for row in edge_rows
    ]
    return {
        "workspace_id": workspace_id,
        "nodes": output_nodes,
        "edges": output_edges,
        "builds": [
            {
                "dataset_id": row["dataset_id"],
                "build_id": row["build_id"],
                "status": row["status"],
                "node_count": row["node_count"],
                "edge_count": row["edge_count"],
                "built_at": row["built_at"],
            }
            for row in build_rows
        ],
    }


def delete_dataset_graph(
    dataset_id: str,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    database_url: str | None = None,
) -> None:
    """Remove a dataset's graph and relationships touching it atomically."""
    dataset_id = _required_string(dataset_id, "dataset_id")
    workspace_id = _required_string(
        workspace_id,
        "workspace_id",
        maximum_length=80,
    )
    try:
        initialize_database(database_url)
        engine = get_database_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                delete(graph_edges).where(
                    and_(
                        graph_edges.c.workspace_id == workspace_id,
                        or_(
                            graph_edges.c.source_dataset_id == dataset_id,
                            graph_edges.c.target_dataset_id == dataset_id,
                        ),
                    )
                )
            )
            connection.execute(
                delete(graph_evidence).where(
                    and_(
                        graph_evidence.c.workspace_id == workspace_id,
                        graph_evidence.c.dataset_id == dataset_id,
                    )
                )
            )
            connection.execute(
                delete(graph_nodes).where(
                    and_(
                        graph_nodes.c.workspace_id == workspace_id,
                        graph_nodes.c.dataset_id == dataset_id,
                    )
                )
            )
            connection.execute(
                delete(graph_builds).where(
                    and_(
                        graph_builds.c.workspace_id == workspace_id,
                        graph_builds.c.dataset_id == dataset_id,
                    )
                )
            )
    except SQLAlchemyError as exc:
        logger.exception("Persistent graph deletion failed for dataset.")
        raise GraphPersistenceError(
            "Could not remove the dataset's persistent knowledge graph."
        ) from exc
