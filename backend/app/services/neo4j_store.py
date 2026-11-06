"""Neo4j persistence for GraphSheet AI graph projections."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)
WRITE_BATCH_SIZE = 500
CONNECTION_TIMEOUT_SECONDS = 5
FAILURE_COOLDOWN_SECONDS = 10
CONNECTIVITY_CACHE_SECONDS = 30


class Neo4jStoreError(RuntimeError):
    """Raised when a configured Neo4j graph cannot be read or written."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "NEO4J_UNAVAILABLE",
        retry_after_seconds: int | None = None,
    ) -> None:
        """Carry a safe error code and optional retry delay with the failure."""
        super().__init__(message)
        self.code = code
        self.retry_after_seconds = retry_after_seconds

    def as_dict(self) -> dict[str, Any]:
        """Return a credential-free, structured API error."""
        error = {"code": self.code, "message": str(self)}
        if self.retry_after_seconds is not None:
            error["retry_after_seconds"] = self.retry_after_seconds
        return error


_lock = threading.RLock()
_driver: Any | None = None
_driver_key: tuple[str, str, str, str] | None = None
_failure_until = 0.0
_last_failure: Neo4jStoreError | None = None
_failure_key: tuple[str, str, str, str] | None = None
_verified_at = 0.0


def is_configured() -> bool:
    """Return whether any Neo4j connection setting has been supplied."""
    return any(
        os.getenv(name, "").strip()
        for name in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    )


def graph_store_status() -> str:
    """Identify the configured graph backend without testing connectivity."""
    return "neo4j" if is_configured() else "sqlite"


def _configuration() -> tuple[str, str, str, str] | None:
    """Validate optional Neo4j settings without exposing secret values."""
    uri = os.getenv("NEO4J_URI", "").strip()
    username = os.getenv("NEO4J_USERNAME", "").strip()
    password = os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("NEO4J_DATABASE", "").strip() or "neo4j"

    if not any((uri, username, password)):
        return None
    if not uri or not username or not password:
        raise Neo4jStoreError(
            "Configure NEO4J_URI, NEO4J_USERNAME, and NEO4J_PASSWORD together.",
            code="NEO4J_CONFIGURATION_INCOMPLETE",
        )

    parsed = urlsplit(uri)
    if parsed.scheme not in {"neo4j", "neo4j+s", "bolt", "bolt+s"}:
        raise Neo4jStoreError(
            "NEO4J_URI must use neo4j://, neo4j+s://, bolt://, or bolt+s://.",
            code="NEO4J_INVALID_URI",
        )
    if not parsed.hostname or parsed.username or parsed.password:
        raise Neo4jStoreError(
            "NEO4J_URI must contain a server hostname and must not embed credentials.",
            code="NEO4J_INVALID_URI",
        )
    if parsed.hostname.endswith(".databases.neo4j.io") and parsed.scheme not in {
        "neo4j+s",
        "bolt+s",
    }:
        raise Neo4jStoreError(
            "Neo4j Aura requires an encrypted neo4j+s:// or bolt+s:// URI.",
            code="NEO4J_TLS_REQUIRED",
        )
    return uri, username, password, database


def connection_info() -> dict[str, Any]:
    """Return safe connection metadata; never expose credentials or hostname."""
    configuration = _configuration()
    if configuration is None:
        return {
            "configured": False,
            "deployment": None,
            "uri_scheme": None,
            "database": None,
        }

    uri, _, _, database = configuration
    parsed = urlsplit(uri)
    is_aura = bool(
        parsed.hostname
        and parsed.hostname.endswith(".databases.neo4j.io")
    )
    return {
        "configured": True,
        "deployment": "aura" if is_aura else "self_hosted_or_remote",
        "uri_scheme": parsed.scheme,
        "database": database,
    }


def _error_for(exc: Exception) -> Neo4jStoreError:
    """Translate driver errors to stable, credential-free diagnostics."""
    chain: list[Exception] = []
    current: BaseException | None = exc
    while isinstance(current, Exception) and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    error_names = {type(error).__name__ for error in chain}
    detail = " ".join(str(error) for error in chain).casefold()
    if error_names.intersection({"AuthError", "Unauthorized"}):
        return Neo4jStoreError(
            "Neo4j rejected the configured credentials. Verify the username and password.",
            code="NEO4J_AUTHENTICATION_FAILED",
        )
    if error_names.intersection(
        {
            "BoltSecurityError",
            "SSLError",
            "SSLCertVerificationError",
            "ConnectionResetError",
        }
    ) or "ssl" in detail or "tls" in detail or "connection reset" in detail:
        return Neo4jStoreError(
            "The secure Neo4j connection was reset during TLS negotiation. "
            "Verify the Aura instance state, network allowlist, and outbound "
            "TLS access to port 7687.",
            code="NEO4J_TLS_CONNECTION_FAILED",
            retry_after_seconds=FAILURE_COOLDOWN_SECONDS,
        )
    if "routing" in detail:
        return Neo4jStoreError(
            "Neo4j did not provide a routing table. Verify the configured "
            "driver URI, instance availability, and network access.",
            code="NEO4J_ROUTING_UNAVAILABLE",
            retry_after_seconds=FAILURE_COOLDOWN_SECONDS,
        )
    return Neo4jStoreError(
        "Neo4j is unavailable. Verify the instance state and network access "
        "to the configured driver endpoint.",
        code="NEO4J_UNAVAILABLE",
        retry_after_seconds=FAILURE_COOLDOWN_SECONDS,
    )


def _get_driver(configuration: tuple[str, str, str, str]) -> Any:
    """Reuse a driver for unchanged settings and replace it when they change."""
    global _driver, _driver_key, _verified_at
    with _lock:
        if _driver is not None and _driver_key != configuration:
            _driver.close()
            _driver = None
            _driver_key = None
        if _failure_key is not None and _failure_key != configuration:
            _record_success()
        if _driver is None:
            uri, username, password, _ = configuration
            try:
                _driver = GraphDatabase.driver(
                    uri,
                    auth=(username, password),
                    connection_timeout=CONNECTION_TIMEOUT_SECONDS,
                    max_transaction_retry_time=CONNECTION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                raise _record_failure(exc, configuration) from exc
            _driver_key = configuration
            _verified_at = 0.0
        return _driver


def _raise_if_cooling_down(
    configuration: tuple[str, str, str, str],
) -> None:
    """Return the recent sanitized error instead of retrying a known outage."""
    if (
        _failure_key == configuration
        and _last_failure is not None
        and time.monotonic() < _failure_until
    ):
        remaining = max(1, int(_failure_until - time.monotonic()))
        raise Neo4jStoreError(
            str(_last_failure),
            code=_last_failure.code,
            retry_after_seconds=remaining,
        )


def _record_success() -> None:
    """Clear the cached failure state after connectivity has been verified."""
    global _failure_until, _last_failure, _failure_key, _verified_at
    with _lock:
        _failure_until = 0.0
        _last_failure = None
        _failure_key = None
        _verified_at = time.monotonic()


def _record_failure(
    exc: Exception,
    configuration: tuple[str, str, str, str],
) -> Neo4jStoreError:
    """Cache and log a sanitized failure once per cooldown period."""
    global _failure_until, _last_failure, _failure_key, _verified_at
    error = _error_for(exc)
    now = time.monotonic()
    with _lock:
        should_log = _last_failure is None or now >= _failure_until
        _failure_until = now + FAILURE_COOLDOWN_SECONDS
        _last_failure = error
        _failure_key = configuration
        _verified_at = 0.0
    if should_log:
        logger.warning(
            "Neo4j connection failed (%s); retry suppressed for %s seconds.",
            error.code,
            FAILURE_COOLDOWN_SECONDS,
        )
    return error


def _ensure_connected(configuration: tuple[str, str, str, str]) -> Any:
    """Check Neo4j connectivity at a bounded interval before using the driver."""
    global _verified_at
    with _lock:
        _raise_if_cooling_down(configuration)
        driver = _get_driver(configuration)
        if time.monotonic() - _verified_at < CONNECTIVITY_CACHE_SECONDS:
            return driver
        try:
            driver.verify_connectivity()
        except Exception as exc:
            raise _record_failure(exc, configuration) from exc
        _record_success()
        return driver


def check_connectivity() -> dict[str, Any]:
    """Verify configured Neo4j connectivity and report safe diagnostics."""
    info = connection_info()
    if not info["configured"]:
        return {
            **info,
            "status": "not_configured",
            "ready": False,
        }

    configuration = _configuration()
    assert configuration is not None
    try:
        driver = _ensure_connected(configuration)
        with driver.session(database=configuration[3]) as session:
            session.run("RETURN 1 AS ready").single()
    except Neo4jStoreError:
        raise
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()
    return {
        **info,
        "status": "ready",
        "ready": True,
    }


def initialize_driver() -> None:
    """Initialize the process-scoped driver lazily, without network I/O."""
    configuration = _configuration()
    if configuration is not None:
        _get_driver(configuration)


def close_driver() -> None:
    """Close the process-scoped driver during application shutdown."""
    global _driver, _driver_key, _failure_until, _last_failure
    global _failure_key, _verified_at
    with _lock:
        driver = _driver
        _driver = None
        _driver_key = None
        _failure_until = 0.0
        _last_failure = None
        _failure_key = None
        _verified_at = 0.0
    if driver is not None:
        driver.close()


def _graph_rows(
    graph: dict[str, Any],
    scope_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a graph payload and serialize nodes and edges for Neo4j."""
    nodes: list[dict[str, Any]] = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id", node.get("node_id"))
        if not isinstance(node_id, str) or not node_id:
            continue
        node_type = node.get("type", node.get("node_type", "Node"))
        label = node.get("label", node_id)
        properties = {
            key: value
            for key, value in node.items()
            if key not in {
                "id", "node_id", "type", "node_type", "label", "created_at"
            }
        }
        nodes.append(
            {
                "id": node_id,
                "scope_id": scope_id,
                "node_type": str(node_type),
                "label": str(label),
                "created_at": str(node.get("created_at", "")),
                "dataset_id": properties.get("dataset_id"),
                "properties_json": json.dumps(
                    properties,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            }
        )

    dataset_by_node_id = {
        node["id"]: node.get("dataset_id")
        for node in nodes
    }
    edges: list[dict[str, Any]] = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        edge_id = edge.get("id", edge.get("edge_id"))
        source_id = edge.get("source", edge.get("source_id"))
        target_id = edge.get("target", edge.get("target_id"))
        if not all(
            isinstance(value, str) and value
            for value in (edge_id, source_id, target_id)
        ):
            continue
        properties = {
            key: value
            for key, value in edge.items()
            if key not in {
                "id", "edge_id", "source", "source_id", "target",
                "target_id", "type", "relationship",
            }
        }
        edges.append(
            {
                "id": edge_id,
                "scope_id": scope_id,
                "source_id": source_id,
                "target_id": target_id,
                "source_dataset_id": dataset_by_node_id.get(source_id),
                "target_dataset_id": dataset_by_node_id.get(target_id),
                "relationship": str(
                    edge.get("type", edge.get("relationship", "RELATED_TO"))
                ),
                "created_at": str(edge.get("created_at", "")),
                "properties_json": json.dumps(
                    properties,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
            }
        )
    return nodes, edges


def sync_graph(scope_id: str, graph: dict[str, Any]) -> bool:
    """Atomically upsert one graph scope and remove stale records."""
    configuration = _configuration()
    if configuration is None:
        return False

    nodes, edges = _graph_rows(graph, scope_id)
    node_ids = [node["id"] for node in nodes]
    edge_ids = [edge["id"] for edge in edges]
    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            session.run(
                "CREATE CONSTRAINT graph_node_scope_id IF NOT EXISTS "
                "FOR (n:GraphNode) REQUIRE (n.scope_id, n.id) IS UNIQUE"
            ).consume()
            session.run(
                "CREATE CONSTRAINT graph_edge_scope_id IF NOT EXISTS "
                "FOR (e:GraphEdge) REQUIRE (e.scope_id, e.id) IS UNIQUE"
            ).consume()

            def write_graph(transaction: Any) -> None:
                """Write one bounded batch of graph nodes and edges transactionally."""
                for start in range(0, len(nodes), WRITE_BATCH_SIZE):
                    transaction.run(
                        """
                        UNWIND $nodes AS row
                        MERGE (node:GraphNode {scope_id: row.scope_id, id: row.id})
                        SET node.node_type = row.node_type,
                            node.label = row.label,
                            node.created_at = row.created_at,
                            node.dataset_id = row.dataset_id,
                            node.properties_json = row.properties_json
                        """,
                        nodes=nodes[start:start + WRITE_BATCH_SIZE],
                    ).consume()
                transaction.run(
                    """
                    MATCH (edge:GraphEdge {scope_id: $scope_id})
                    WHERE NOT edge.id IN $edge_ids
                    DETACH DELETE edge
                    """,
                    scope_id=scope_id,
                    edge_ids=edge_ids,
                ).consume()
                transaction.run(
                    """
                    UNWIND $edge_ids AS edge_id
                    MATCH (edge:GraphEdge {scope_id: $scope_id, id: edge_id})
                    OPTIONAL MATCH (edge)-[link]-()
                    DELETE link
                    """,
                    scope_id=scope_id,
                    edge_ids=edge_ids,
                ).consume()
                for start in range(0, len(edges), WRITE_BATCH_SIZE):
                    transaction.run(
                        """
                        UNWIND $edges AS row
                        MATCH (source:GraphNode {scope_id: row.scope_id, id: row.source_id})
                        MATCH (target:GraphNode {scope_id: row.scope_id, id: row.target_id})
                        MERGE (edge:GraphEdge {scope_id: row.scope_id, id: row.id})
                        SET edge.relationship = row.relationship,
                            edge.created_at = row.created_at,
                            edge.properties_json = row.properties_json
                        MERGE (source)-[:SOURCE]->(edge)
                        MERGE (edge)-[:TARGET]->(target)
                        """,
                        edges=edges[start:start + WRITE_BATCH_SIZE],
                    ).consume()
                transaction.run(
                    """
                    MATCH (node:GraphNode)
                    WHERE NOT node.id IN $node_ids
                    DETACH DELETE node
                    """,
                    scope_id=scope_id,
                    node_ids=node_ids,
                ).consume()

            session.execute_write(write_graph)
    except Neo4jStoreError:
        raise
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()
    return True


def get_graph(scope_id: str) -> dict[str, Any]:
    """Load one isolated graph scope from Neo4j."""
    configuration = _configuration()
    if configuration is None:
        raise Neo4jStoreError(
            "Neo4j is not configured.",
            code="NEO4J_NOT_CONFIGURED",
        )

    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            node_rows = session.run(
                """
                MATCH (node:GraphNode {scope_id: $scope_id})
                RETURN node.id AS id, node.node_type AS type,
                       node.label AS label, node.created_at AS created_at,
                       node.properties_json AS properties_json
                ORDER BY node.created_at, node.id
                """,
                scope_id=scope_id,
            ).data()
            edge_rows = session.run(
                """
                MATCH (source:GraphNode {scope_id: $scope_id})
                      -[:SOURCE]->(edge:GraphEdge {scope_id: $scope_id})
                      -[:TARGET]->(target:GraphNode {scope_id: $scope_id})
                RETURN edge.id AS id, source.id AS source,
                       target.id AS target, edge.relationship AS type,
                       edge.properties_json AS properties_json
                ORDER BY edge.created_at, edge.id
                """,
                scope_id=scope_id,
            ).data()
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()
    return {
        "scope_id": scope_id,
        "nodes": [
            {
                **json.loads(row["properties_json"]),
                "id": row["id"],
                "type": row["type"],
                "label": row["label"],
                "created_at": row["created_at"],
            }
            for row in node_rows
        ],
        "edges": [
            {
                **json.loads(row["properties_json"]),
                "id": row["id"],
                "source": row["source"],
                "target": row["target"],
                "type": row["type"],
            }
            for row in edge_rows
        ],
    }


DATASET_GRAPH_SCOPE = "dataset-knowledge-graph"


def _dataset_graph_scope_id(dataset_ids: list[str]) -> str:
    """Keep full and chat-selected graph projections isolated in Neo4j."""
    digest = hashlib.sha256(
        "\x1f".join(sorted(dataset_ids)).encode("utf-8")
    ).hexdigest()[:20]
    return f"{DATASET_GRAPH_SCOPE}:{digest}"


def replace_dataset_graph(
    graph: dict[str, Any],
    dataset_ids: list[str],
) -> None:
    """Replace selected dataset graph records in Neo4j without touching others."""
    configuration = _configuration()
    if configuration is None:
        raise Neo4jStoreError(
            "Neo4j is not configured.",
            code="NEO4J_NOT_CONFIGURED",
        )
    if not isinstance(dataset_ids, list) or any(
        not isinstance(dataset_id, str) or not dataset_id.strip()
        for dataset_id in dataset_ids
    ):
        raise Neo4jStoreError("Dataset graph IDs must be non-empty strings.")
    selected_ids = list(dict.fromkeys(item.strip() for item in dataset_ids))
    if not selected_ids:
        return
    if not isinstance(graph, dict):
        raise Neo4jStoreError("Dataset graph data must be an object.")

    scope_id = _dataset_graph_scope_id(selected_ids)
    nodes, edges = _graph_rows(graph, scope_id)
    if any(node.get("dataset_id") not in selected_ids for node in nodes):
        raise Neo4jStoreError(
            "A Neo4j graph node is outside the selected dataset scope."
        )
    node_ids = {node["id"] for node in nodes}
    if any(
        edge["source_id"] not in node_ids or edge["target_id"] not in node_ids
        or edge["source_dataset_id"] not in selected_ids
        or edge["target_dataset_id"] not in selected_ids
        for edge in edges
    ):
        raise Neo4jStoreError(
            "A Neo4j graph edge is outside the selected dataset scope."
        )

    counts = {
        dataset_id: {
            "node_count": sum(
                node.get("dataset_id") == dataset_id for node in nodes
            ),
            "edge_count": sum(
                edge["source_dataset_id"] == dataset_id for edge in edges
            ),
        }
        for dataset_id in selected_ids
    }
    builds = [
        {
            "scope_id": DATASET_GRAPH_SCOPE,
            "dataset_id": dataset_id,
            "status": "complete",
            **counts[dataset_id],
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        for dataset_id in selected_ids
    ]

    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            session.run(
                "CREATE CONSTRAINT graph_dataset_build_scope_id IF NOT EXISTS "
                "FOR (b:GraphBuild) REQUIRE (b.scope_id, b.dataset_id) IS UNIQUE"
            ).consume()

            def write_graph(transaction: Any) -> None:
                """Replace only selected dataset nodes and incident relationships."""
                transaction.run(
                    """
                    MATCH (edge:GraphEdge {scope_id: $scope_id})
                    WHERE edge.source_dataset_id IN $dataset_ids
                       OR edge.target_dataset_id IN $dataset_ids
                    DETACH DELETE edge
                    """,
                    scope_id=scope_id,
                    dataset_ids=selected_ids,
                ).consume()
                transaction.run(
                    """
                    MATCH (node:GraphNode {scope_id: $scope_id})
                    WHERE node.dataset_id IN $dataset_ids
                    DETACH DELETE node
                    """,
                    scope_id=scope_id,
                    dataset_ids=selected_ids,
                ).consume()
                transaction.run(
                    """
                    UNWIND $dataset_ids AS dataset_id
                    MATCH (build:GraphBuild {
                        scope_id: $scope_id,
                        dataset_id: dataset_id
                    })
                    DELETE build
                    """,
                    scope_id=scope_id,
                    dataset_ids=selected_ids,
                ).consume()
                for start in range(0, len(nodes), WRITE_BATCH_SIZE):
                    transaction.run(
                        """
                        UNWIND $nodes AS row
                        MERGE (node:GraphNode {scope_id: row.scope_id, id: row.id})
                        SET node.node_type = row.node_type,
                            node.label = row.label,
                            node.created_at = row.created_at,
                            node.dataset_id = row.dataset_id,
                            node.properties_json = row.properties_json
                        """,
                        nodes=nodes[start:start + WRITE_BATCH_SIZE],
                    ).consume()
                for start in range(0, len(edges), WRITE_BATCH_SIZE):
                    transaction.run(
                        """
                        UNWIND $edges AS row
                        MATCH (source:GraphNode {
                            scope_id: row.scope_id,
                            id: row.source_id
                        })
                        MATCH (target:GraphNode {
                            scope_id: row.scope_id,
                            id: row.target_id
                        })
                        MERGE (edge:GraphEdge {scope_id: row.scope_id, id: row.id})
                        SET edge.relationship = row.relationship,
                            edge.created_at = row.created_at,
                            edge.dataset_id = row.source_dataset_id,
                            edge.source_dataset_id = row.source_dataset_id,
                            edge.target_dataset_id = row.target_dataset_id,
                            edge.properties_json = row.properties_json
                        MERGE (source)-[:SOURCE]->(edge)
                        MERGE (edge)-[:TARGET]->(target)
                        """,
                        edges=edges[start:start + WRITE_BATCH_SIZE],
                    ).consume()
                transaction.run(
                    """
                    UNWIND $builds AS row
                    MERGE (build:GraphBuild {
                        scope_id: row.scope_id,
                        dataset_id: row.dataset_id
                    })
                    SET build.status = row.status,
                        build.node_count = row.node_count,
                        build.edge_count = row.edge_count,
                        build.built_at = row.built_at
                    """,
                    builds=builds,
                ).consume()

            session.execute_write(write_graph)
    except Neo4jStoreError:
        raise
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()


def get_dataset_graph(
    dataset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read dataset nodes, graph edges, and build markers from Neo4j."""
    configuration = _configuration()
    if configuration is None:
        raise Neo4jStoreError(
            "Neo4j is not configured.",
            code="NEO4J_NOT_CONFIGURED",
        )
    if dataset_ids is not None and (
        not isinstance(dataset_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in dataset_ids)
    ):
        raise Neo4jStoreError("Dataset graph IDs must be non-empty strings.")
    selected_ids = (
        list(dict.fromkeys(item.strip() for item in dataset_ids))
        if dataset_ids is not None
        else None
    )
    scope_id = (
        _dataset_graph_scope_id(selected_ids)
        if selected_ids is not None
        else None
    )
    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            node_rows = session.run(
                """
                MATCH (node:GraphNode {scope_id: $scope_id})
                WHERE (
                    ($scope_id IS NOT NULL AND node.scope_id = $scope_id)
                    OR (
                        $scope_id IS NULL
                        AND node.scope_id STARTS WITH $scope_prefix
                    )
                )
                RETURN node.id AS id, node.node_type AS type,
                       node.label AS label, node.created_at AS created_at,
                       node.properties_json AS properties_json
                ORDER BY node.created_at, node.id
                """,
                scope_id=scope_id,
                scope_prefix=f"{DATASET_GRAPH_SCOPE}:",
            ).data()
            edge_rows = session.run(
                """
                MATCH (source:GraphNode)
                      -[:SOURCE]->(edge:GraphEdge)
                      -[:TARGET]->(target:GraphNode)
                WHERE edge.scope_id = source.scope_id
                  AND edge.scope_id = target.scope_id
                  AND (
                    ($scope_id IS NOT NULL AND edge.scope_id = $scope_id)
                    OR (
                        $scope_id IS NULL
                        AND edge.scope_id STARTS WITH $scope_prefix
                    )
                )
                RETURN edge.id AS id, source.id AS source,
                       target.id AS target, edge.relationship AS type,
                       edge.properties_json AS properties_json
                ORDER BY edge.created_at, edge.id
                """,
                scope_id=scope_id,
                scope_prefix=f"{DATASET_GRAPH_SCOPE}:",
            ).data()
            build_rows = session.run(
                """
                MATCH (build:GraphBuild)
                WHERE (
                    ($scope_id IS NOT NULL AND build.scope_id = $scope_id)
                    OR (
                        $scope_id IS NULL
                        AND build.scope_id STARTS WITH $scope_prefix
                    )
                )
                RETURN build.dataset_id AS dataset_id,
                       build.status AS status,
                       build.node_count AS node_count,
                       build.edge_count AS edge_count,
                       build.built_at AS built_at
                ORDER BY build.dataset_id
                """,
                scope_id=scope_id,
                scope_prefix=f"{DATASET_GRAPH_SCOPE}:",
            ).data()
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()
    output_nodes = []
    for row in node_rows:
        node = {
            **json.loads(row["properties_json"]),
            "id": row["id"],
            "type": row["type"],
            "label": row["label"],
        }
        if row["created_at"]:
            node["created_at"] = row["created_at"]
        output_nodes.append(node)

    return {
        "scope_id": scope_id or DATASET_GRAPH_SCOPE,
        "nodes": output_nodes,
        "edges": [
            {
                **json.loads(row["properties_json"]),
                "id": row["id"],
                "source": row["source"],
                "target": row["target"],
                "type": row["type"],
            }
            for row in edge_rows
        ],
        "builds": [
            {
                "dataset_id": row["dataset_id"],
                "status": row["status"],
                "node_count": row["node_count"],
                "edge_count": row["edge_count"],
                "built_at": row["built_at"],
            }
            for row in build_rows
        ],
    }


def delete_dataset_graph(dataset_id: str) -> None:
    """Delete one dataset and all incident candidate links from the Neo4j graph."""
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise Neo4jStoreError("Dataset ID must be a non-empty string.")
    configuration = _configuration()
    if configuration is None:
        raise Neo4jStoreError(
            "Neo4j is not configured.",
            code="NEO4J_NOT_CONFIGURED",
        )
    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            def remove_dataset(transaction: Any) -> None:
                """Delete graph data and build metadata scoped to one dataset."""
                scope_result = transaction.run(
                    """
                    MATCH (build:GraphBuild {dataset_id: $dataset_id})
                    WHERE build.scope_id STARTS WITH $scope_prefix
                    RETURN collect(DISTINCT build.scope_id) AS scope_ids
                    """,
                    scope_prefix=f"{DATASET_GRAPH_SCOPE}:",
                    dataset_id=dataset_id,
                ).single()
                scope_ids = (
                    scope_result.get("scope_ids", [])
                    if scope_result
                    else []
                )
                if not scope_ids:
                    return
                transaction.run(
                    """
                    MATCH (edge:GraphEdge)
                    WHERE edge.scope_id IN $scope_ids
                    DETACH DELETE edge
                    """,
                    scope_ids=scope_ids,
                    dataset_id=dataset_id,
                ).consume()
                transaction.run(
                    """
                    MATCH (node:GraphNode)
                    WHERE node.scope_id IN $scope_ids
                    DETACH DELETE node
                    """,
                    scope_ids=scope_ids,
                    dataset_id=dataset_id,
                ).consume()
                transaction.run(
                    """
                    MATCH (build:GraphBuild)
                    WHERE build.scope_id IN $scope_ids
                    DELETE build
                    """,
                    scope_ids=scope_ids,
                    dataset_id=dataset_id,
                ).consume()

            session.execute_write(remove_dataset)
    except Neo4jStoreError:
        raise
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()


def delete_graphs(scope_ids: list[str]) -> None:
    """Delete graph scopes in one Neo4j transaction."""
    configuration = _configuration()
    if configuration is None:
        return
    normalized_scope_ids = list(dict.fromkeys(scope_ids))
    if not normalized_scope_ids:
        return

    driver = _ensure_connected(configuration)
    try:
        with driver.session(database=configuration[3]) as session:
            def remove_scopes(transaction: Any) -> None:
                """Delete graph records only for the requested scopes."""
                transaction.run(
                    """
                    UNWIND $scope_ids AS scope_id
                    MATCH (edge:GraphEdge {scope_id: scope_id})
                    DETACH DELETE edge
                    """,
                    scope_ids=normalized_scope_ids,
                ).consume()
                transaction.run(
                    """
                    UNWIND $scope_ids AS scope_id
                    MATCH (node:GraphNode {scope_id: scope_id})
                    DETACH DELETE node
                    """,
                    scope_ids=normalized_scope_ids,
                ).consume()

            session.execute_write(remove_scopes)
    except Exception as exc:
        raise _record_failure(exc, configuration) from exc
    _record_success()


def delete_graph(scope_id: str) -> None:
    """Delete one graph scope."""
    delete_graphs([scope_id])
