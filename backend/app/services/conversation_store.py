"""Minimal SQLite persistence for GraphSheet AI conversations and events."""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Generator, TypeVar

from backend.app.core.database import (
    database_url_for_path,
    initialize_database,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "graphsheet.db"
logger = logging.getLogger(__name__)
SQLITE_TIMEOUT_SECONDS = 5
SQLITE_BUSY_TIMEOUT_MS = SQLITE_TIMEOUT_SECONDS * 1000
SQLITE_WRITE_RETRIES = 3
_WAL_DATABASES: set[Path] = set()
_WAL_LOCK = threading.Lock()
_SCHEMA_DATABASES: set[Path] = set()
_SCHEMA_LOCK = threading.RLock()
_WriteResult = TypeVar("_WriteResult")


class ConversationStoreError(RuntimeError):
    """Raised when conversation persistence fails."""

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


@contextmanager
def _connection(
    database_path: str | Path,
    *,
    immediate: bool = False,
) -> Generator[sqlite3.Connection, None, None]:
    """Open, transact, and always close a SQLite connection."""
    connection = sqlite3.connect(
        database_path,
        timeout=SQLITE_TIMEOUT_SECONDS,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _enable_wal(database_path: str | Path) -> None:
    """Enable WAL once per local database to reduce reader/writer contention."""
    database = Path(database_path).resolve()
    with _WAL_LOCK:
        if database in _WAL_DATABASES:
            return
        connection = sqlite3.connect(
            database,
            timeout=SQLITE_TIMEOUT_SECONDS,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
            journal_mode = connection.execute(
                "PRAGMA journal_mode = WAL"
            ).fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise ConversationStoreError(
                    "Could not enable SQLite WAL mode for conversation storage.",
                    code="CONVERSATION_STORAGE_CONFIGURATION",
                )
            _WAL_DATABASES.add(database)
        except sqlite3.Error as exc:
            raise ConversationStoreError(
                "Could not configure SQLite conversation storage.",
                code="CONVERSATION_STORAGE_CONFIGURATION",
            ) from exc
        finally:
            connection.close()


def _retry_locked_writes(
    function: Callable[..., _WriteResult],
) -> Callable[..., _WriteResult]:
    """Retry a complete, rolled-back write only when SQLite reports lock contention."""
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> _WriteResult:
        for attempt in range(SQLITE_WRITE_RETRIES + 1):
            try:
                return function(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                locked = "locked" in str(exc).casefold() or "busy" in str(exc).casefold()
                if not locked:
                    raise ConversationStoreError(
                        "Could not save the conversation event.",
                        code="CONVERSATION_WRITE_FAILED",
                    ) from exc
                if attempt == SQLITE_WRITE_RETRIES:
                    raise ConversationStoreError(
                        "Conversation storage is busy. Please retry shortly.",
                        code="SQLITE_BUSY",
                        retry_after_seconds=2,
                    ) from exc
                delay = min(0.05 * (2**attempt), 0.4)
                time.sleep(random.uniform(delay * 0.75, delay * 1.25))
            except sqlite3.Error as exc:
                raise ConversationStoreError(
                    "Could not save the conversation event.",
                    code="CONVERSATION_WRITE_FAILED",
                ) from exc
        raise ConversationStoreError(
            "Could not save the conversation event.",
            code="CONVERSATION_WRITE_FAILED",
        )

    return wrapped


def _sync_neo4j_graph(conversation_id: str, database_path: str | Path) -> None:
    """Project the SQLite conversation graph into Neo4j when configured."""
    from backend.app.services.neo4j_store import (
        Neo4jStoreError,
        is_configured,
        sync_graph,
    )

    if not is_configured():
        return
    try:
        graph = _get_sqlite_conversation_graph(conversation_id, database_path)
        sync_graph(conversation_id, graph)
    except Neo4jStoreError as exc:
        logger.warning(
            "Neo4j conversation graph sync failed; SQLite remains authoritative (%s).",
            exc.code,
        )


def _utc_timestamp() -> str:
    """Return an ISO 8601 UTC timestamp without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_tables(database_path: str | Path) -> None:
    """Initialize a conversation database once per process."""
    database = Path(database_path).resolve()
    with _SCHEMA_LOCK:
        if database in _SCHEMA_DATABASES:
            return
        _initialize_tables(database)
        _SCHEMA_DATABASES.add(database)


def _initialize_tables(database_path: str | Path) -> None:
    """Create the conversation tables if they do not already exist."""
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    _enable_wal(database)
    initialize_database(database_url_for_path(database))

    with _connection(database, immediate=True) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                dataset_id TEXT,
                selected_dataset_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_events (
                event_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                message TEXT,
                metadata_json TEXT,
                result_reference TEXT,
                created_at TEXT NOT NULL,
                event_order INTEGER NOT NULL,
                idempotency_key TEXT,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
            """
        )
        event_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(conversation_events)"
            )
        }
        if "idempotency_key" not in event_columns:
            connection.execute(
                "ALTER TABLE conversation_events ADD COLUMN idempotency_key TEXT"
            )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_events_idempotency
            ON conversation_events (conversation_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_events_order
            ON conversation_events (conversation_id, event_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_events_type
            ON conversation_events (conversation_id, event_type, created_at)
            """
        )
        conversation_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(conversations)"
            )
        }
        if "selected_dataset_ids" not in conversation_columns:
            connection.execute(
                """
                ALTER TABLE conversations
                ADD COLUMN selected_dataset_ids TEXT NOT NULL DEFAULT '[]'
                """
            )
            connection.execute(
                """
                UPDATE conversations
                SET selected_dataset_ids = json_array(dataset_id)
                WHERE dataset_id IS NOT NULL
                """
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_graph_nodes (
                node_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_graph_nodes_conversation
            ON conversation_graph_nodes (conversation_id, node_type)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_graph_edges (
                edge_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (source_id)
                    REFERENCES conversation_graph_nodes(node_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (target_id)
                    REFERENCES conversation_graph_nodes(node_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conversation_graph_edges_conversation
            ON conversation_graph_edges (conversation_id, relationship)
            """
        )


def _upsert_graph_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    conversation_id: str,
    node_type: str,
    label: str,
    properties: dict[str, Any],
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO conversation_graph_nodes (
            node_id, conversation_id, node_type, label, properties_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            label = excluded.label,
            properties_json = excluded.properties_json
        """,
        (
            node_id,
            conversation_id,
            node_type,
            label,
            json.dumps(properties, ensure_ascii=False, separators=(",", ":")),
            created_at,
        ),
    )


def _upsert_graph_edge(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    conversation_id: str,
    source_id: str,
    target_id: str,
    relationship: str,
    properties: dict[str, Any] | None = None,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO conversation_graph_edges (
            edge_id, conversation_id, source_id, target_id,
            relationship, properties_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(edge_id) DO UPDATE SET
            properties_json = excluded.properties_json
        """,
        (
            edge_id,
            conversation_id,
            source_id,
            target_id,
            relationship,
            json.dumps(properties or {}, ensure_ascii=False, separators=(",", ":")),
            created_at,
        ),
    )


def _ensure_chat_graph_node(
    connection: sqlite3.Connection,
    conversation_id: str,
    title: str,
    timestamp: str,
) -> None:
    _upsert_graph_node(
        connection,
        node_id=f"chat:{conversation_id}",
        conversation_id=conversation_id,
        node_type="Chat",
        label=title,
        properties={
            "conversation_id": conversation_id,
            "title": title,
        },
        created_at=timestamp,
    )


def _write_selected_datasets(
    connection: sqlite3.Connection,
    conversation_id: str,
    dataset_ids: list[str],
    timestamp: str,
) -> None:
    chat_node_id = f"chat:{conversation_id}"
    connection.execute(
        """
        DELETE FROM conversation_graph_edges
        WHERE conversation_id = ? AND relationship = 'USES_DATASET'
        """,
        (conversation_id,),
    )
    for dataset_id in dataset_ids:
        dataset_node_id = f"dataset:{conversation_id}:{dataset_id}"
        dataset_metadata = {}
        table_rows = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "datasets" in table_rows:
            row = connection.execute(
                """
                SELECT original_filename, table_name
                FROM datasets
                WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchone()
            if row is not None:
                dataset_metadata = {
                    "filename": row["original_filename"],
                    "table_name": row["table_name"],
                }
        _upsert_graph_node(
            connection,
            node_id=dataset_node_id,
            conversation_id=conversation_id,
            node_type="Dataset",
            label=dataset_metadata.get("filename", dataset_id),
            properties={
                "dataset_id": dataset_id,
                **dataset_metadata,
            },
            created_at=timestamp,
        )
        if "dataset_columns" in table_rows:
            columns = connection.execute(
                """
                SELECT original_name, sql_name, data_type
                FROM dataset_columns
                WHERE dataset_id = ?
                ORDER BY ordinal
                """,
                (dataset_id,),
            ).fetchall()
            for column in columns:
                column_node_id = (
                    f"column:{conversation_id}:{dataset_id}:"
                    f"{column['sql_name']}"
                )
                _upsert_graph_node(
                    connection,
                    node_id=column_node_id,
                    conversation_id=conversation_id,
                    node_type="Column",
                    label=column["original_name"],
                    properties={
                        "dataset_id": dataset_id,
                        "sql_name": column["sql_name"],
                        "data_type": column["data_type"],
                    },
                    created_at=timestamp,
                )
                _upsert_graph_edge(
                    connection,
                    edge_id=(
                        f"dataset-column:{conversation_id}:"
                        f"{dataset_id}:{column['sql_name']}"
                    ),
                    conversation_id=conversation_id,
                    source_id=dataset_node_id,
                    target_id=column_node_id,
                    relationship="HAS_COLUMN",
                    created_at=timestamp,
                )
        _upsert_graph_edge(
            connection,
            edge_id=f"chat-dataset:{conversation_id}:{dataset_id}",
            conversation_id=conversation_id,
            source_id=chat_node_id,
            target_id=dataset_node_id,
            relationship="USES_DATASET",
            created_at=timestamp,
        )


def validate_dataset_ids(
    dataset_ids: list[str],
    database_path: str | Path = DEFAULT_DATABASE,
) -> list[str]:
    """Validate and normalize a chat's selected dataset IDs."""
    if not isinstance(dataset_ids, list):
        raise ValueError("Selected dataset IDs must be a list.")
    if len(dataset_ids) > 5:
        raise ValueError("A chat can use at most five datasets.")
    if any(not isinstance(value, str) or not value.strip() for value in dataset_ids):
        raise ValueError("Dataset IDs must be non-empty strings.")

    normalized = list(dict.fromkeys(value.strip() for value in dataset_ids))
    if len(normalized) > 5:
        raise ValueError("A chat can use at most five datasets.")
    if not normalized:
        return normalized

    database = Path(database_path)
    try:
        with _connection(database) as connection:
            registered_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "datasets" not in registered_tables:
                raise ConversationStoreError(
                    "No uploaded datasets are registered."
                )
            placeholders = ", ".join("?" for _ in normalized)
            rows = connection.execute(
                "SELECT dataset_id FROM datasets "
                f"WHERE dataset_id IN ({placeholders})",
                tuple(normalized),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ConversationStoreError(
            "Could not validate the selected datasets."
        ) from exc

    found = {row[0] for row in rows}
    missing = [dataset_id for dataset_id in normalized if dataset_id not in found]
    if missing:
        raise ConversationStoreError(
            "One or more selected datasets do not exist."
        )
    return normalized


def set_conversation_datasets(
    conversation_id: str,
    dataset_ids: list[str],
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Replace the datasets selected for a conversation."""
    selected_ids = validate_dataset_ids(dataset_ids, database_path)
    _ensure_tables(database_path)
    timestamp = _utc_timestamp()

    with _connection(database_path, immediate=True) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT title FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if exists is None:
            raise ConversationStoreError("Conversation not found.")

        connection.execute(
            """
            UPDATE conversations
            SET dataset_id = ?, selected_dataset_ids = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (
                selected_ids[0] if selected_ids else None,
                json.dumps(selected_ids, separators=(",", ":")),
                timestamp,
                conversation_id,
            ),
        )
        _write_selected_datasets(
            connection,
            conversation_id,
            selected_ids,
            timestamp,
        )
        _ensure_chat_graph_node(
            connection,
            conversation_id,
            exists["title"],
            timestamp,
        )

    _sync_neo4j_graph(conversation_id, database_path)
    from backend.app.services.neo4j_store import (
        Neo4jStoreError,
        delete_graph,
    )
    try:
        delete_graph(f"chat-schema:{conversation_id}")
    except Neo4jStoreError as exc:
        logger.warning(
            "Neo4j selected-sheet graph cleanup failed; it will be rebuilt from SQLite (%s).",
            exc.code,
        )
    return {
        "conversation_id": conversation_id,
        "dataset_id": selected_ids[0] if selected_ids else None,
        "selected_dataset_ids": selected_ids,
        "updated_at": timestamp,
    }


def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
    """Decode a database event row to a plain dictionary."""
    metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    event = {
        "event_id": row["event_id"],
        "conversation_id": row["conversation_id"],
        "event_type": row["event_type"],
        "actor": row["actor"],
        "message": row["message"],
        "metadata": metadata,
        "result_reference": row["result_reference"],
        "created_at": row["created_at"],
        "event_order": row["event_order"],
    }
    if event["result_reference"] is None and isinstance(metadata, dict):
        event["result_reference"] = metadata.get("result_reference")
    return event


def create_conversation(
    title: str | None = None,
    dataset_id: str | None = None,
    selected_dataset_ids: list[str] | None = None,
    conversation_id: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Create a conversation and return its metadata."""
    database = Path(database_path)
    _ensure_tables(database)

    title_value = (title or "New conversation").strip() or "New conversation"
    selected_ids = list(selected_dataset_ids or [])
    if dataset_id and dataset_id not in selected_ids:
        selected_ids.insert(0, dataset_id)
    if len(selected_ids) > 5:
        raise ValueError("A chat can use at most five datasets.")
    if any(not isinstance(value, str) or not value.strip() for value in selected_ids):
        raise ValueError("Dataset IDs must be non-empty strings.")
    selected_ids = list(dict.fromkeys(value.strip() for value in selected_ids))
    if len(selected_ids) > 5:
        raise ValueError("A chat can use at most five datasets.")
    primary_dataset_id = selected_ids[0] if selected_ids else None
    if conversation_id is not None:
        try:
            conversation_id = str(uuid.UUID(conversation_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("Conversation ID must be a valid UUID.") from exc
        existing = None
        if database.is_file():
            with _connection(database) as connection:
                existing = connection.execute(
                    """
                    SELECT 1 FROM conversations WHERE conversation_id = ?
                    """,
                    (conversation_id,),
                ).fetchone()
        if existing is not None:
            return get_conversation(conversation_id, database)
    else:
        conversation_id = uuid.uuid4().hex

    timestamp = _utc_timestamp()

    with _connection(database, immediate=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO conversations (
                conversation_id,
                title,
                dataset_id,
                selected_dataset_ids,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                title_value,
                primary_dataset_id,
                json.dumps(selected_ids, separators=(",", ":")),
                "active",
                timestamp,
                timestamp,
            ),
        )
        _ensure_chat_graph_node(
            connection,
            conversation_id,
            title_value,
            timestamp,
        )
        _write_selected_datasets(
            connection,
            conversation_id,
            selected_ids,
            timestamp,
        )

    _sync_neo4j_graph(conversation_id, database)
    return {
        "conversation_id": conversation_id,
        "title": title_value,
        "dataset_id": primary_dataset_id,
        "selected_dataset_ids": selected_ids,
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
        "events": [],
    }


def list_conversations(
    database_path: str | Path = DEFAULT_DATABASE,
) -> list[dict[str, Any]]:
    """Return the list of stored conversations in reverse chron order."""
    database = Path(database_path)
    _ensure_tables(database)

    with _connection(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT conversation_id, title, dataset_id, selected_dataset_ids,
                   status, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()

    return [
        {
            "conversation_id": row["conversation_id"],
            "title": row["title"],
            "dataset_id": row["dataset_id"],
            "selected_dataset_ids": json.loads(row["selected_dataset_ids"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_conversation(
    conversation_id: str,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Return a conversation and all of its stored events."""
    database = Path(database_path)
    _ensure_tables(database)

    with _connection(database) as connection:
        connection.row_factory = sqlite3.Row
        conversation = connection.execute(
            """
            SELECT conversation_id, title, dataset_id, selected_dataset_ids,
                   status, created_at, updated_at
            FROM conversations
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()

        if conversation is None:
            raise ConversationStoreError("Conversation not found.")

        events = connection.execute(
            """
            SELECT event_id, conversation_id, event_type, actor, message,
                   metadata_json, result_reference, created_at, event_order
            FROM conversation_events
            WHERE conversation_id = ?
            ORDER BY event_order ASC, created_at ASC
            """,
            (conversation_id,),
        ).fetchall()

    result = {
        "conversation_id": conversation["conversation_id"],
        "title": conversation["title"],
        "dataset_id": conversation["dataset_id"],
        "selected_dataset_ids": json.loads(
            conversation["selected_dataset_ids"]
        ),
        "status": conversation["status"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
        "events": [_event_from_row(row) for row in events],
    }
    return result


@_retry_locked_writes
def record_event(
    conversation_id: str,
    event_type: str,
    *,
    actor: str = "assistant",
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
    result_reference: str | None = None,
    idempotency_key: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a single conversation event."""
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("Conversation ID must not be empty.")

    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("Event type must not be empty.")

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Actor must not be empty.")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 200
    ):
        raise ValueError(
            "Idempotency key must be a non-empty string of at most 200 characters."
        )

    database = Path(database_path)
    _ensure_tables(database)

    with _connection(database, immediate=True) as connection:
        metadata_payload: dict[str, Any] = dict(metadata or {})
        if result_reference is not None:
            metadata_payload.setdefault("result_reference", result_reference)
        if result_reference is not None and message is None:
            message = f"result:{result_reference}"
        metadata_json = (
            json.dumps(
                metadata_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if metadata_payload
            else None
        )

        if idempotency_key is not None:
            existing = connection.execute(
                """
                SELECT event_id, conversation_id, event_type, actor, message,
                       metadata_json, result_reference, created_at, event_order
                FROM conversation_events
                WHERE conversation_id = ? AND idempotency_key = ?
                """,
                (conversation_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event_type
                    or existing["actor"] != actor
                    or existing["message"] != message
                    or existing["metadata_json"] != metadata_json
                    or existing["result_reference"] != result_reference
                ):
                    raise ConversationStoreError(
                        "This idempotency key was already used for a different event.",
                        code="IDEMPOTENCY_CONFLICT",
                    )
                return _event_from_row(existing)

        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        if exists is None:
            raise ConversationStoreError("Conversation not found.")

        event_order = connection.execute(
            """
            SELECT COALESCE(MAX(event_order), 0) + 1
            FROM conversation_events
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()[0]
        previous_event = connection.execute(
            """
            SELECT event_id
            FROM conversation_events
            WHERE conversation_id = ?
            ORDER BY event_order DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
        conversation = connection.execute(
            "SELECT title FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        event_id = uuid.uuid4().hex
        created_at = _utc_timestamp()

        connection.execute(
            """
            INSERT INTO conversation_events (
                event_id,
                conversation_id,
                event_type,
                actor,
                message,
                metadata_json,
                result_reference,
                created_at,
                event_order,
                idempotency_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                conversation_id,
                event_type,
                actor,
                message,
                metadata_json,
                result_reference,
                created_at,
                event_order,
                idempotency_key,
            ),
        )

        if isinstance(message, str) and (
            (event_type == "message" and actor == "user")
            or event_type == "upload"
        ):
            current_title = connection.execute(
                """
                SELECT title FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()["title"]
            if current_title == "New chat":
                title = (
                    Path(message).stem
                    if event_type == "upload"
                    else message.strip()[:48]
                )
                connection.execute(
                    """
                    UPDATE conversations
                    SET title = ?
                    WHERE conversation_id = ?
                    """,
                    (title or "New chat", conversation_id),
                )

        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (created_at, conversation_id),
        )

        _ensure_chat_graph_node(
            connection,
            conversation_id,
            conversation["title"],
            created_at,
        )
        node_type = {
            "message": "Message",
            "sql": "SQLQuery",
            "execution": "Execution",
            "result_reference": "QueryResult",
        }.get(event_type, event_type.replace("_", " ").title())
        node_id = f"event:{event_id}"
        label = (
            message
            or metadata_payload.get("question")
            or metadata_payload.get("sql")
            or event_type
        )
        _upsert_graph_node(
            connection,
            node_id=node_id,
            conversation_id=conversation_id,
            node_type=node_type,
            label=str(label),
            properties={
                "metadata": metadata_payload,
                **metadata_payload,
                "event_id": event_id,
                "event_type": event_type,
                "actor": actor,
                "message": message,
                "result_reference": result_reference,
                "event_order": event_order,
            },
            created_at=created_at,
        )
        graph_relationship = {
            "message": "HAS_MESSAGE",
            "sql": "HAS_QUERY",
        }.get(event_type, "HAS_EVENT")
        _upsert_graph_edge(
            connection,
            edge_id=f"chat-event:{conversation_id}:{event_id}",
            conversation_id=conversation_id,
            source_id=f"chat:{conversation_id}",
            target_id=node_id,
            relationship=graph_relationship,
            created_at=created_at,
        )
        if previous_event is not None:
            _upsert_graph_edge(
                connection,
                edge_id=(
                    f"follows:{conversation_id}:"
                    f"{previous_event['event_id']}:{event_id}"
                ),
                conversation_id=conversation_id,
                source_id=f"event:{previous_event['event_id']}",
                target_id=node_id,
                relationship="FOLLOWS",
                created_at=created_at,
            )

        dataset_ids = metadata_payload.get("dataset_ids", [])
        if not isinstance(dataset_ids, list):
            dataset_ids = []
        dataset_id = metadata_payload.get("dataset_id")
        if isinstance(dataset_id, str) and dataset_id not in dataset_ids:
            dataset_ids.append(dataset_id)
        for selected_dataset_id in dataset_ids:
            if not isinstance(selected_dataset_id, str) or not selected_dataset_id:
                continue
            dataset_node_id = (
                f"dataset:{conversation_id}:{selected_dataset_id}"
            )
            _upsert_graph_node(
                connection,
                node_id=dataset_node_id,
                conversation_id=conversation_id,
                node_type="Dataset",
                label=selected_dataset_id,
                properties={"dataset_id": selected_dataset_id},
                created_at=created_at,
            )
            if event_type == "sql":
                _upsert_graph_edge(
                    connection,
                    edge_id=f"query-dataset:{event_id}:{selected_dataset_id}",
                    conversation_id=conversation_id,
                    source_id=node_id,
                    target_id=dataset_node_id,
                    relationship="QUERIES_DATASET",
                    created_at=created_at,
                )
            elif event_type == "upload":
                _upsert_graph_edge(
                    connection,
                    edge_id=f"upload-dataset:{event_id}:{selected_dataset_id}",
                    conversation_id=conversation_id,
                    source_id=node_id,
                    target_id=dataset_node_id,
                    relationship="UPLOADS_DATASET",
                    created_at=created_at,
                )
            elif event_type == "message":
                _upsert_graph_edge(
                    connection,
                    edge_id=f"message-dataset:{event_id}:{selected_dataset_id}",
                    conversation_id=conversation_id,
                    source_id=node_id,
                    target_id=dataset_node_id,
                    relationship="ASKED_ABOUT",
                    created_at=created_at,
                )
            read_columns = metadata_payload.get("read_columns", [])
            read_column_refs = metadata_payload.get("read_column_refs")
            if event_type == "sql" and isinstance(read_columns, list):
                if isinstance(read_column_refs, list):
                    dataset_table = connection.execute(
                        "SELECT table_name FROM datasets WHERE dataset_id = ?",
                        (selected_dataset_id,),
                    ).fetchone()
                    table_name = (
                        dataset_table["table_name"]
                        if dataset_table is not None
                        else None
                    )
                    selected_columns = [
                        reference.get("column")
                        for reference in read_column_refs
                        if isinstance(reference, dict)
                        and reference.get("table") == table_name
                    ]
                else:
                    selected_columns = read_columns
                for column_name in selected_columns:
                    if not isinstance(column_name, str) or not column_name:
                        continue
                    column_node_id = (
                        f"column:{conversation_id}:"
                        f"{selected_dataset_id}:{column_name}"
                    )
                    existing_column = connection.execute(
                        "SELECT 1 FROM conversation_graph_nodes WHERE node_id = ?",
                        (column_node_id,),
                    ).fetchone()
                    if existing_column is None:
                        _upsert_graph_node(
                            connection,
                            node_id=column_node_id,
                            conversation_id=conversation_id,
                            node_type="Column",
                            label=column_name,
                            properties={
                                "dataset_id": selected_dataset_id,
                                "sql_name": column_name,
                            },
                            created_at=created_at,
                        )
                    _upsert_graph_edge(
                        connection,
                        edge_id=(
                            f"dataset-column:{conversation_id}:"
                            f"{selected_dataset_id}:{column_name}"
                        ),
                        conversation_id=conversation_id,
                        source_id=dataset_node_id,
                        target_id=column_node_id,
                        relationship="HAS_COLUMN",
                        created_at=created_at,
                    )
                    _upsert_graph_edge(
                        connection,
                        edge_id=(
                            f"query-column:{event_id}:"
                            f"{selected_dataset_id}:{column_name}"
                        ),
                        conversation_id=conversation_id,
                        source_id=node_id,
                        target_id=column_node_id,
                        relationship="READS_COLUMN",
                        created_at=created_at,
                    )

        sources = metadata_payload.get("sources", [])
        if event_type == "message" and isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                chunk_id = source.get("chunk_id")
                if not isinstance(chunk_id, str) or not chunk_id:
                    continue
                evidence_node_id = (
                    f"evidence:{conversation_id}:{event_id}:{chunk_id}"
                )
                _upsert_graph_node(
                    connection,
                    node_id=evidence_node_id,
                    conversation_id=conversation_id,
                    node_type="Evidence",
                    label=f"Evidence chunk {source.get('chunk_index', '')}",
                    properties=source,
                    created_at=created_at,
                )
                _upsert_graph_edge(
                    connection,
                    edge_id=f"message-evidence:{event_id}:{chunk_id}",
                    conversation_id=conversation_id,
                    source_id=node_id,
                    target_id=evidence_node_id,
                    relationship="SUPPORTED_BY",
                    created_at=created_at,
                )

        query_id = metadata_payload.get("query_id")
        if isinstance(query_id, str):
            query_node_id = f"event:{query_id}"
            query_exists = connection.execute(
                "SELECT 1 FROM conversation_graph_nodes WHERE node_id = ?",
                (query_node_id,),
            ).fetchone()
            if query_exists is not None and event_type in {
                "execution",
                "result_reference",
            }:
                _upsert_graph_edge(
                    connection,
                    edge_id=f"query-event:{query_id}:{event_id}",
                    conversation_id=conversation_id,
                    source_id=query_node_id,
                    target_id=node_id,
                    relationship=(
                        "PRODUCED_RESULT"
                        if event_type == "result_reference"
                        else "HAS_EXECUTION"
                    ),
                    created_at=created_at,
                )

    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "event_type": event_type,
        "actor": actor,
        "message": message,
        "metadata": metadata_payload,
        "result_reference": result_reference,
        "created_at": created_at,
        "event_order": event_order,
    }


def get_conversation_graph(
    conversation_id: str,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Return the persisted graph for one conversation."""
    from backend.app.services.neo4j_store import (
        Neo4jStoreError,
        get_graph,
        is_configured,
        sync_graph,
    )

    if is_configured():
        sqlite_graph = _get_sqlite_conversation_graph(
            conversation_id,
            database_path,
        )
        try:
            sync_graph(conversation_id, sqlite_graph)
            return {
                "conversation_id": conversation_id,
                **get_graph(conversation_id),
            }
        except Neo4jStoreError as exc:
            logger.warning(
                "Neo4j conversation graph unavailable; serving SQLite graph (%s).",
                exc.code,
            )
            return {
                **sqlite_graph,
                "conversation_id": conversation_id,
                "graph_store": "sqlite",
            }
    return _get_sqlite_conversation_graph(conversation_id, database_path)


def _get_sqlite_conversation_graph(
    conversation_id: str,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Read the local graph projection used as the durable event outbox."""
    database = Path(database_path)
    _ensure_tables(database)

    with _connection(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if exists is None:
            raise ConversationStoreError("Conversation not found.")

        node_rows = connection.execute(
            """
            SELECT node_id, node_type, label, properties_json, created_at
            FROM conversation_graph_nodes
            WHERE conversation_id = ?
            ORDER BY created_at, node_id
            """,
            (conversation_id,),
        ).fetchall()
        edge_rows = connection.execute(
            """
            SELECT edge_id, source_id, target_id, relationship, properties_json
            FROM conversation_graph_edges
            WHERE conversation_id = ?
            ORDER BY created_at, edge_id
            """,
            (conversation_id,),
        ).fetchall()

    return {
        "conversation_id": conversation_id,
        "nodes": [
            {
                **json.loads(row["properties_json"]),
                "id": row["node_id"],
                "type": row["node_type"],
                "label": row["label"],
                "conversation_id": conversation_id,
                "created_at": row["created_at"],
            }
            for row in node_rows
        ],
        "edges": [
            {
                **json.loads(row["properties_json"]),
                "id": row["edge_id"],
                "source": row["source_id"],
                "target": row["target_id"],
                "type": row["relationship"],
            }
            for row in edge_rows
        ],
    }


def delete_conversation(
    conversation_id: str,
    database_path: str | Path = DEFAULT_DATABASE,
) -> bool:
    """Delete one conversation and its graph nodes and edges."""
    _ensure_tables(database_path)
    with _connection(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if exists is None:
        return False

    from backend.app.services.neo4j_store import (
        Neo4jStoreError,
        delete_graphs,
    )

    try:
        delete_graphs(
            [conversation_id, f"chat-schema:{conversation_id}"]
        )
    except Neo4jStoreError as exc:
        raise ConversationStoreError(
            str(exc),
            code=exc.code,
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc

    with _connection(database_path, immediate=True) as connection:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        deleted = cursor.rowcount > 0
    return deleted


def get_event_by_idempotency_key(
    conversation_id: str,
    idempotency_key: str,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any] | None:
    """Return a previously committed event for a request idempotency key."""
    database = Path(database_path)
    _ensure_tables(database)
    with _connection(database) as connection:
        row = connection.execute(
            """
            SELECT event_id, conversation_id, event_type, actor, message,
                   metadata_json, result_reference, created_at, event_order
            FROM conversation_events
            WHERE conversation_id = ? AND idempotency_key = ?
            """,
            (conversation_id, idempotency_key),
        ).fetchone()
    return _event_from_row(row) if row is not None else None


def record_message(
    conversation_id: str,
    *,
    actor: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a user or assistant message event."""
    return record_event(
        conversation_id,
        "message",
        actor=actor,
        message=message,
        metadata=metadata,
        idempotency_key=idempotency_key,
        database_path=database_path,
    )


def record_clarification(
    conversation_id: str,
    *,
    actor: str,
    message: str,
    options: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a clarification question and the options shown to the user."""
    payload = dict(metadata or {})
    if options is not None:
        payload["options"] = list(options)
    return record_event(
        conversation_id,
        "clarification",
        actor=actor,
        message=message,
        metadata=payload,
        database_path=database_path,
    )


def record_choice(
    conversation_id: str,
    *,
    actor: str,
    message: str | None,
    selected_option: str,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a user selection from a clarification prompt."""
    payload = dict(metadata or {})
    payload["selected_option"] = selected_option
    return record_event(
        conversation_id,
        "user_choice",
        actor=actor,
        message=message,
        metadata=payload,
        database_path=database_path,
    )


def record_sql(
    conversation_id: str,
    *,
    actor: str,
    sql: str,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist generated SQL for the conversation."""
    payload = dict(metadata or {})
    payload["sql"] = sql
    return record_event(
        conversation_id,
        "sql",
        actor=actor,
        message="Generated SQL",
        metadata=payload,
        database_path=database_path,
    )


def record_execution(
    conversation_id: str,
    *,
    actor: str,
    status: str,
    message: str | None = None,
    result_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist execution status for generated SQL."""
    payload = dict(metadata or {})
    payload["status"] = status
    return record_event(
        conversation_id,
        "execution",
        actor=actor,
        message=message or f"Execution status: {status}",
        metadata=payload,
        result_reference=result_reference,
        database_path=database_path,
    )


def record_result_reference(
    conversation_id: str,
    *,
    actor: str,
    result_reference: str,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a reference to a query result or output artifact."""
    payload = dict(metadata or {})
    payload["result_reference"] = result_reference
    return record_event(
        conversation_id,
        "result_reference",
        actor=actor,
        message=message or "Result reference recorded.",
        metadata=payload,
        result_reference=result_reference,
        database_path=database_path,
    )
