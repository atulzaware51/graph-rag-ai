"""Shared SQLAlchemy engine and persistent graph schema."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine, URL, make_url

from backend.app.core.config import DEFAULT_SQLITE_PATH, get_database_url


metadata = MetaData()

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("dataset_id", String(80), primary_key=True),
    Column("node_id", String(255), primary_key=True),
    Column("node_type", String(80), nullable=False),
    Column("label", Text, nullable=False),
    Column("properties_json", Text, nullable=False),
)
Index(
    "idx_graph_nodes_workspace_dataset_type",
    graph_nodes.c.workspace_id,
    graph_nodes.c.dataset_id,
    graph_nodes.c.node_type,
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("dataset_id", String(80), primary_key=True),
    Column("edge_id", String(255), primary_key=True),
    Column("source_dataset_id", String(80), nullable=False),
    Column("source_id", String(255), nullable=False),
    Column("target_dataset_id", String(80), nullable=False),
    Column("target_id", String(255), nullable=False),
    Column("relationship", String(120), nullable=False),
    Column("properties_json", Text, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "source_dataset_id", "source_id"],
        [
            "graph_nodes.workspace_id",
            "graph_nodes.dataset_id",
            "graph_nodes.node_id",
        ],
        ondelete="CASCADE",
        name="fk_graph_edges_source_node",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "target_dataset_id", "target_id"],
        [
            "graph_nodes.workspace_id",
            "graph_nodes.dataset_id",
            "graph_nodes.node_id",
        ],
        ondelete="CASCADE",
        name="fk_graph_edges_target_node",
    ),
)
Index(
    "idx_graph_edges_workspace_dataset_relationship",
    graph_edges.c.workspace_id,
    graph_edges.c.dataset_id,
    graph_edges.c.relationship,
)
Index(
    "idx_graph_edges_workspace_endpoints",
    graph_edges.c.workspace_id,
    graph_edges.c.source_dataset_id,
    graph_edges.c.source_id,
    graph_edges.c.target_dataset_id,
    graph_edges.c.target_id,
)

graph_evidence = Table(
    "graph_evidence",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("dataset_id", String(80), primary_key=True),
    Column("evidence_id", String(255), primary_key=True),
    Column("node_id", String(255), nullable=False),
    Column("source_file", Text),
    Column("sheet_name", Text),
    Column("source_columns_json", Text, nullable=False),
    Column("source_rows_json", Text, nullable=False),
    Column("evidence_text", Text, nullable=False),
    Column("metadata_json", Text, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "dataset_id", "node_id"],
        ["graph_nodes.workspace_id", "graph_nodes.dataset_id", "graph_nodes.node_id"],
        ondelete="CASCADE",
        name="fk_graph_evidence_node",
    ),
)
Index(
    "idx_graph_evidence_workspace_dataset",
    graph_evidence.c.workspace_id,
    graph_evidence.c.dataset_id,
)

graph_builds = Table(
    "graph_builds",
    metadata,
    Column("workspace_id", String(80), primary_key=True),
    Column("dataset_id", String(80), primary_key=True),
    Column("build_id", String(1024), nullable=False),
    Column("status", String(32), nullable=False),
    Column("node_count", Integer, nullable=False),
    Column("edge_count", Integer, nullable=False),
    Column("built_at", String(40), nullable=False),
)

_GRAPH_TABLES = {
    "graph_nodes": graph_nodes,
    "graph_edges": graph_edges,
    "graph_evidence": graph_evidence,
    "graph_builds": graph_builds,
}
_ENGINES: set[Engine] = set()


def database_url_for_path(database_path: str | Path) -> str:
    """Create a SQLite URL for an explicit local database file."""
    path = Path(database_path).resolve()
    return URL.create("sqlite", database=str(path)).render_as_string(
        hide_password=False
    )


def graph_database_url_for_source(database_path: str | Path) -> str:
    """Use DATABASE_URL for the default store and a test/source SQLite file otherwise."""
    if Path(database_path).resolve() == DEFAULT_SQLITE_PATH.resolve():
        return get_database_url()
    return database_url_for_path(database_path)


def _normalize_database_url(database_url: str | None) -> str:
    url = database_url or get_database_url()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache(maxsize=8)
def _engine_for_url(normalized_url: str) -> Engine:
    url = make_url(normalized_url)
    if url.get_backend_name() == "sqlite":
        database = url.database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
        )
        _ENGINES.add(engine)

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            _connection_record: Any,
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    engine = create_engine(url, pool_pre_ping=True)
    _ENGINES.add(engine)
    return engine


def get_database_engine(database_url: str | None = None) -> Engine:
    """Return a cached engine for the configured or supplied database URL."""
    return _engine_for_url(_normalize_database_url(database_url))


def initialize_database(database_url: str | None = None) -> None:
    """Create graph tables and migrate legacy conversation graph tables safely."""
    engine = get_database_engine(database_url)
    if engine.dialect.name == "sqlite":
        _migrate_legacy_conversation_graph(engine)
    metadata.create_all(engine, tables=list(_GRAPH_TABLES.values()))


def _migrate_legacy_conversation_graph(engine: Engine) -> None:
    """Rename conversation-only graph tables before the dataset graph schema is created."""
    with engine.begin() as connection:
        existing_tables = set(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).scalars()
        )
        if "graph_nodes" in existing_tables and "conversation_graph_nodes" not in existing_tables:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    'PRAGMA table_info("graph_nodes")'
                )
            }
            if "conversation_id" in columns and "workspace_id" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "graph_nodes" RENAME TO "conversation_graph_nodes"'
                )
        if "graph_edges" in existing_tables and "conversation_graph_edges" not in existing_tables:
            columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    'PRAGMA table_info("graph_edges")'
                )
            }
            if "conversation_id" in columns and "workspace_id" not in columns:
                connection.exec_driver_sql(
                    'ALTER TABLE "graph_edges" RENAME TO "conversation_graph_edges"'
                )


def dispose_database_engines() -> None:
    """Dispose cached engines, primarily for reconnect tests and shutdown."""
    for engine in _ENGINES:
        engine.dispose()
    _ENGINES.clear()
    _engine_for_url.cache_clear()


def graph_table(name: str) -> Table:
    """Return a known graph table by its stable table name."""
    try:
        return _GRAPH_TABLES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown graph table: {name}") from exc
