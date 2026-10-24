"""Minimal SQLite persistence for GraphSheet AI conversations and events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "graphsheet.db"


class ConversationStoreError(RuntimeError):
    """Raised when conversation persistence fails."""


def _utc_timestamp() -> str:
    """Return an ISO 8601 UTC timestamp without microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_tables(database_path: str | Path) -> None:
    """Create the conversation tables if they do not already exist."""
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                dataset_id TEXT,
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
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE
            )
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
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Create a conversation and return its metadata."""
    database = Path(database_path)
    _ensure_tables(database)

    title_value = (title or "New conversation").strip() or "New conversation"
    timestamp = _utc_timestamp()
    conversation_id = uuid.uuid4().hex

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO conversations (
                conversation_id,
                title,
                dataset_id,
                status,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                title_value,
                dataset_id,
                "active",
                timestamp,
                timestamp,
            ),
        )

    return {
        "conversation_id": conversation_id,
        "title": title_value,
        "dataset_id": dataset_id,
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

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT conversation_id, title, dataset_id, status, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC, created_at DESC
            """
        ).fetchall()

    return [
        {
            "conversation_id": row["conversation_id"],
            "title": row["title"],
            "dataset_id": row["dataset_id"],
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

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        conversation = connection.execute(
            """
            SELECT conversation_id, title, dataset_id, status, created_at, updated_at
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
        "status": conversation["status"],
        "created_at": conversation["created_at"],
        "updated_at": conversation["updated_at"],
        "events": [_event_from_row(row) for row in events],
    }
    return result


def record_event(
    conversation_id: str,
    event_type: str,
    *,
    actor: str = "assistant",
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
    result_reference: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a single conversation event."""
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("Conversation ID must not be empty.")

    if not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("Event type must not be empty.")

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Actor must not be empty.")

    database = Path(database_path)
    _ensure_tables(database)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()

        if exists is None:
            raise ConversationStoreError("Conversation not found.")

        metadata_payload: dict[str, Any] = dict(metadata or {})
        if result_reference is not None:
            metadata_payload.setdefault("result_reference", result_reference)
        if result_reference is not None and message is None:
            message = f"result:{result_reference}"

        event_order = connection.execute(
            """
            SELECT COALESCE(MAX(event_order), 0) + 1
            FROM conversation_events
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()[0]

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
                event_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                conversation_id,
                event_type,
                actor,
                message,
                json.dumps(metadata_payload, ensure_ascii=False, separators=(",", ":"))
                if metadata_payload
                else None,
                result_reference,
                created_at,
                event_order,
            ),
        )

        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (created_at, conversation_id),
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


def record_message(
    conversation_id: str,
    *,
    actor: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    database_path: str | Path = DEFAULT_DATABASE,
) -> dict[str, Any]:
    """Persist a user or assistant message event."""
    return record_event(
        conversation_id,
        "message",
        actor=actor,
        message=message,
        metadata=metadata,
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
