"""Persistent ChromaDB vector search with a SQLite embedding cache."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any

from backend.app.services.embedding_service import (
    EmbeddingError,
    embed_texts,
    embedding_model_name,
)


class VectorStoreError(RuntimeError):
    """Raised when local vector data cannot be stored or searched."""


_CHROMA_COLLECTION_NAME = "graphsheet_evidence"
_CHROMA_LOCK = threading.RLock()
_CHROMA_CLIENT: Any | None = None
_CHROMA_COLLECTIONS: dict[str, Any] = {}
_CHROMA_PATH: Path | None = None


def _get_chroma_client() -> Any:
    """Return the persistent local Chroma client, creating it on first use."""
    global _CHROMA_CLIENT, _CHROMA_COLLECTIONS, _CHROMA_PATH
    configured_path = os.getenv("CHROMA_PERSIST_DIRECTORY", "").strip()
    project_root = Path(__file__).resolve().parents[3]
    persist_path = (
        Path(configured_path)
        if configured_path
        else project_root / "data" / "chroma"
    )
    if not persist_path.is_absolute():
        persist_path = project_root / persist_path
    persist_path = persist_path.resolve()
    with _CHROMA_LOCK:
        if _CHROMA_CLIENT is not None and _CHROMA_PATH == persist_path:
            return _CHROMA_CLIENT
        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError as exc:
            raise VectorStoreError(
                "ChromaDB is required for vector search. Install project dependencies."
            ) from exc
        try:
            persist_path.mkdir(parents=True, exist_ok=True)
            _CHROMA_CLIENT = chromadb.PersistentClient(
                path=str(persist_path),
                settings=Settings(anonymized_telemetry=False),
            )
        except Exception as exc:
            raise VectorStoreError(
                "Could not initialize the persistent Chroma vector index."
            ) from exc
        _CHROMA_COLLECTIONS = {}
        _CHROMA_PATH = persist_path
        return _CHROMA_CLIENT


def _get_chroma_collection(dimensions: int) -> Any:
    """Return a cosine collection dedicated to one embedding dimension."""
    collection_name = f"{_CHROMA_COLLECTION_NAME}_{dimensions}"
    with _CHROMA_LOCK:
        collection = _CHROMA_COLLECTIONS.get(collection_name)
        if collection is not None:
            return collection
        client = _get_chroma_client()
        try:
            collection = client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            raise VectorStoreError(
                "Could not initialize the persistent Chroma vector collection."
            ) from exc
        _CHROMA_COLLECTIONS[collection_name] = collection
        return collection


def create_vector_table(connection: sqlite3.Connection) -> None:
    """Create the local embedding cache and Chroma indexing state tables."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_vectors (
            chunk_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            FOREIGN KEY (chunk_id)
                REFERENCES document_chunks(chunk_id)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chunk_vectors_dataset
        ON chunk_vectors(dataset_id)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS chroma_index_state (
            dataset_id TEXT PRIMARY KEY,
            model_name TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL
        )
        """
    )
    existing_columns = {
        row[1]
        for row in connection.execute(
            'PRAGMA table_info("chroma_index_state")'
        )
    }
    if "dimensions" not in existing_columns:
        connection.execute(
            """
            ALTER TABLE chroma_index_state
            ADD COLUMN dimensions INTEGER NOT NULL DEFAULT 0
            """
        )


def insert_embeddings(
    connection: sqlite3.Connection,
    dataset_id: str,
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    *,
    model_name: str,
) -> None:
    """Validate and pack normalized vectors before linking them to stored chunks."""
    if len(chunks) != len(vectors):
        raise VectorStoreError("Embedding count does not match the chunk count.")
    if not chunks:
        return
    dimensions = len(vectors[0])
    if dimensions < 1:
        raise VectorStoreError("Embeddings must have at least one dimension.")
    rows = []
    for chunk, vector in zip(chunks, vectors):
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise VectorStoreError("Embedding vectors have inconsistent dimensions.")
        if any(not math.isfinite(float(value)) for value in vector):
            raise VectorStoreError("Embedding vectors contain non-finite values.")
        chunk_id = chunk.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            raise VectorStoreError("Every embedded chunk must have a chunk ID.")
        packed = struct.pack(f"<{dimensions}f", *(float(value) for value in vector))
        rows.append((chunk_id, dataset_id, model_name, dimensions, packed))
    create_vector_table(connection)
    connection.executemany(
        """
        INSERT INTO chunk_vectors (
            chunk_id, dataset_id, model_name, dimensions, vector
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
            dataset_id = excluded.dataset_id,
            model_name = excluded.model_name,
            dimensions = excluded.dimensions,
            vector = excluded.vector
        """,
        rows,
    )


def _ensure_dataset_embeddings(
    dataset_id: str,
    database_path: str | Path,
) -> list[dict[str, Any]]:
    """Index missing chunks or rebuild vectors when the configured model changes."""
    database = Path(database_path)
    if not database.is_file():
        raise VectorStoreError("The dataset database could not be found.")

    from backend.app.services.dataset_store import (
        DatasetStorageError,
        get_dataset_chunks,
        update_dataset_embedding_status,
    )

    try:
        chunks = get_dataset_chunks(dataset_id, database)
    except DatasetStorageError as exc:
        raise VectorStoreError(
            "Could not load chunks for local vector indexing."
        ) from exc
    if not chunks:
        return []

    model_name = embedding_model_name()
    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            create_vector_table(connection)
            stored = connection.execute(
                """
                SELECT chunk_id, model_name, dimensions
                FROM chunk_vectors
                WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise VectorStoreError("Could not read the local vector index.") from exc
    chunk_ids = {chunk["chunk_id"] for chunk in chunks}
    stored_by_id = {row["chunk_id"]: row for row in stored}
    needs_reindex = any(
        row["model_name"] != model_name
        for row in stored
        if row["chunk_id"] in chunk_ids
    )
    if needs_reindex:
        missing_chunks = chunks
    else:
        missing_chunks = [
            chunk for chunk in chunks
            if chunk["chunk_id"] not in stored_by_id
        ]
    if missing_chunks:
        try:
            vectors = embed_texts([chunk["text"] for chunk in missing_chunks])
        except EmbeddingError as exc:
            raise VectorStoreError(str(exc)) from exc
        try:
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                insert_embeddings(
                    connection,
                    dataset_id,
                    missing_chunks,
                    vectors,
                    model_name=model_name,
                )
        except sqlite3.Error as exc:
            raise VectorStoreError("Could not persist chunk embeddings.") from exc
    try:
        update_dataset_embedding_status(
            dataset_id,
            "complete",
            database,
        )
    except DatasetStorageError as exc:
        raise VectorStoreError(
            "Could not persist the dataset embedding status."
        ) from exc
    return chunks


def search_similar_chunks(
    query: str,
    dataset_ids: list[str],
    top_k: int = 5,
    database_path: str | Path = "data/graphsheet.db",
) -> list[dict[str, Any]]:
    """Return selected-dataset chunks ranked by Chroma cosine similarity."""
    if not isinstance(query, str) or not query.strip():
        raise VectorStoreError("Vector search query must not be empty.")
    if (
        not isinstance(dataset_ids, list)
        or not dataset_ids
        or any(
            not isinstance(dataset_id, str) or not dataset_id.strip()
            for dataset_id in dataset_ids
        )
    ):
        raise VectorStoreError("Select at least one dataset for vector search.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise VectorStoreError("Vector search top_k must be a positive integer.")

    chunks_by_dataset = {
        dataset_id: _ensure_dataset_embeddings(dataset_id, database_path)
        for dataset_id in dict.fromkeys(dataset_ids)
    }
    if not any(chunks_by_dataset.values()):
        return []

    database = Path(database_path)
    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            create_vector_table(connection)
            for dataset_id, chunks in chunks_by_dataset.items():
                if chunks:
                    _ensure_chroma_dataset_index(
                        connection,
                        dataset_id,
                        chunks,
                    )
    except sqlite3.Error as exc:
        raise VectorStoreError("Could not prepare the Chroma vector index.") from exc

    try:
        query_vector = embed_texts([query])[0]
    except EmbeddingError as exc:
        raise VectorStoreError(str(exc)) from exc
    collection = _get_chroma_collection(len(query_vector))
    selected_ids = list(chunks_by_dataset)
    try:
        result = collection.query(
            query_embeddings=[query_vector],
            n_results=min(
                top_k,
                sum(len(chunks) for chunks in chunks_by_dataset.values()),
            ),
            where={
                "$and": [
                    {"dataset_id": {"$in": selected_ids}},
                    {"model_name": embedding_model_name()},
                ]
            },
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        raise VectorStoreError("Could not search the Chroma vector index.") from exc

    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    ranked = []
    for index, chunk_id in enumerate(ids):
        metadata = metadatas[index] or {}
        try:
            chunk_metadata = json.loads(metadata.get("metadata_json", "{}"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise VectorStoreError(
                "Chroma returned invalid chunk metadata."
            ) from exc
        if not isinstance(chunk_metadata, dict):
            raise VectorStoreError("Chroma returned invalid chunk metadata.")
        distance = float(distances[index])
        ranked.append(
            {
                "chunk_id": chunk_id,
                "dataset_id": metadata["dataset_id"],
                "chunk_index": int(metadata["chunk_index"]),
                "text": documents[index] or "",
                "metadata": chunk_metadata,
                "vector_similarity": 1.0 - distance,
            }
        )
    return ranked


def _ensure_chroma_dataset_index(
    connection: sqlite3.Connection,
    dataset_id: str,
    chunks: list[dict[str, Any]],
) -> None:
    """Copy cached embeddings into Chroma once per dataset and model version."""
    model_name = embedding_model_name()
    rows = connection.execute(
        """
        SELECT vectors.chunk_id, vectors.model_name, vectors.dimensions,
               vectors.vector, chunks.chunk_index, chunks.text,
               chunks.metadata_json
        FROM chunk_vectors AS vectors
        JOIN document_chunks AS chunks
          ON chunks.chunk_id = vectors.chunk_id
        WHERE vectors.dataset_id = ?
        ORDER BY chunks.chunk_index
        """,
        (dataset_id,),
    ).fetchall()
    if len(rows) != len(chunks):
        raise VectorStoreError(
            "The local embedding cache does not match the dataset chunks."
        )
    dimensions = rows[0]["dimensions"]
    if dimensions < 1:
        raise VectorStoreError("The local embedding cache has invalid dimensions.")
    if any(row["dimensions"] != dimensions for row in rows):
        raise VectorStoreError(
            "The local embedding cache contains inconsistent dimensions."
        )
    collection = _get_chroma_collection(dimensions)
    state = connection.execute(
        """
        SELECT model_name, dimensions, chunk_count
        FROM chroma_index_state
        WHERE dataset_id = ?
        """,
        (dataset_id,),
    ).fetchone()
    dataset_filter = {
        "$and": [
            {"dataset_id": dataset_id},
            {"model_name": model_name},
        ]
    }
    try:
        existing_ids = collection.get(
            where=dataset_filter,
            limit=1,
            include=[],
        ).get("ids", [])
    except Exception as exc:
        raise VectorStoreError("Could not inspect the Chroma vector index.") from exc
    if (
        state is not None
        and state["model_name"] == model_name
        and state["dimensions"] == dimensions
        and state["chunk_count"] == len(chunks)
        and existing_ids
    ):
        return

    if (
        state is not None
        and state["dimensions"] > 0
        and state["dimensions"] != dimensions
    ):
        previous_collection = _get_chroma_collection(state["dimensions"])
        try:
            previous_collection.delete(where={"dataset_id": dataset_id})
        except Exception as exc:
            raise VectorStoreError(
                "Could not replace stale Chroma embeddings."
            ) from exc
    try:
        collection.delete(where={"dataset_id": dataset_id})
    except Exception as exc:
        raise VectorStoreError(
            "Could not replace stale Chroma embeddings."
        ) from exc

    ids = []
    documents = []
    embeddings = []
    metadatas = []
    for row in rows:
        dimensions = row["dimensions"]
        if (
            row["model_name"] != model_name
            or dimensions < 1
            or len(row["vector"]) != dimensions * 4
        ):
            raise VectorStoreError(
                "The local embedding cache is incompatible with the configured model."
            )
        ids.append(row["chunk_id"])
        documents.append(row["text"])
        embeddings.append(list(struct.unpack(f"<{dimensions}f", row["vector"])))
        metadatas.append(
            {
                "dataset_id": dataset_id,
                "chunk_index": int(row["chunk_index"]),
                "metadata_json": row["metadata_json"],
                "model_name": model_name,
            }
        )

    try:
        for start in range(0, len(ids), 500):
            collection.upsert(
                ids=ids[start:start + 500],
                documents=documents[start:start + 500],
                embeddings=embeddings[start:start + 500],
                metadatas=metadatas[start:start + 500],
            )
        indexed_ids = collection.get(
            where=dataset_filter,
            include=[],
        ).get("ids", [])
        if len(indexed_ids) != len(chunks):
            raise VectorStoreError(
                "Chroma did not index every dataset chunk."
            )
        connection.execute(
            """
            INSERT INTO chroma_index_state (
                dataset_id, model_name, dimensions, chunk_count
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
                model_name = excluded.model_name,
                dimensions = excluded.dimensions,
                chunk_count = excluded.chunk_count
            """,
            (dataset_id, model_name, dimensions, len(chunks)),
        )
    except VectorStoreError:
        raise
    except Exception as exc:
        raise VectorStoreError("Could not persist embeddings in Chroma.") from exc


def delete_dataset_vectors(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> None:
    """Remove a dataset's Chroma records before deleting its source chunks."""
    database = Path(database_path)
    if not database.is_file():
        return
    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'chroma_index_state'
                """
            ).fetchone()
            if exists is None:
                return
            state = connection.execute(
                """
                SELECT dimensions FROM chroma_index_state
                WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchone()
            if state is None:
                return
            collection = _get_chroma_collection(state["dimensions"])
            collection.delete(where={"dataset_id": dataset_id})
            connection.execute(
                "DELETE FROM chroma_index_state WHERE dataset_id = ?",
                (dataset_id,),
            )
    except sqlite3.Error as exc:
        raise VectorStoreError(
            "Could not remove the dataset's Chroma index state."
        ) from exc
    except VectorStoreError:
        raise
    except Exception as exc:
        raise VectorStoreError(
            "Could not remove the dataset's Chroma embeddings."
        ) from exc


def get_vector_stats(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> dict[str, int | str]:
    """Return persisted embedding count and vector dimensions."""
    database = Path(database_path)
    if not database.is_file():
        return {"embedding_count": 0, "dimensions": 0}
    try:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(dimensions), 0),
                       COALESCE(MAX(model_name), '')
                FROM chunk_vectors WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise VectorStoreError("Could not read vector index status.") from exc
    return {
        "embedding_count": int(row[0]),
        "dimensions": int(row[1]),
        "model_name": row[2],
    }
