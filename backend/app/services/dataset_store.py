# Import json standard library module for handling JSON serialization and deserialization
import json
# Import hashlib for stable spreadsheet-content fingerprints.
import hashlib
# Import re standard library module for regular expression operations (text cleaning/slugification)
import re
# Import sqlite3 standard library module for interaction with SQLite database engine
import sqlite3
# Import uuid module for generating unique hexadecimal identifier strings
import uuid
# Import datetime and timezone for ISO 8601 UTC timestamp generation
from datetime import datetime, timezone
# Import Path object from pathlib for object-oriented filesystem path manipulation
from pathlib import Path
# Import Any type hint for flexible dictionary and list type annotations
from typing import Any

# Import pandas library for tabular data reading and manipulation
import pandas as pd
import xlrd

from backend.app.core.database import graph_database_url_for_source
from backend.app.services.graph_store import (
    GraphPersistenceError,
    delete_dataset_graph,
)


# Define set of acceptable/supported file extensions for dataset storage
SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx"}
DATASET_STATUS_COLUMNS = {
    "upload_status": "complete",
    "parsing_status": "complete",
    "storage_status": "complete",
    "chunk_status": "complete",
    "graph_build_status": "pending",
    "graph_updated_at": None,
    "graph_error": None,
}


# Custom exception class inheriting from Exception to handle dataset storage errors safely
class DatasetStorageError(Exception):
    """Raised when a dataset cannot be stored safely."""


# Helper function to read spreadsheet files into a pandas DataFrame based on extension
def _read_dataframe(file_path: Path, extension: str) -> pd.DataFrame:
    # Read CSV file using standard pandas read_csv method
    if extension == ".csv":
        return pd.read_csv(file_path)

    if extension == ".xls":
        return pd.read_excel(file_path, engine="xlrd")

    # Read Excel file using pandas read_excel with openpyxl engine
    return pd.read_excel(file_path, engine="openpyxl")


# Helper function to sanitize raw spreadsheet column names into valid, unique SQL identifiers
def _safe_column_names(columns: list[Any]) -> list[str]:
    """Convert spreadsheet column names into unique SQL identifiers."""
    # List to store converted SQL-safe column names
    result = []
    # Set tracking already generated column names to guarantee uniqueness
    used = set()

    # Loop through each column name with 1-based index
    for index, original_name in enumerate(columns, start=1):
        # Convert column name to string, strip whitespace, and convert to lowercase
        name = str(original_name).strip().lower()
        # Replace any character that is not lowercase alphanumeric or underscore with an underscore
        name = re.sub(r"[^a-z0-9_]+", "_", name)
        # Collapse multiple consecutive underscores into a single underscore and strip edge underscores
        name = re.sub(r"_+", "_", name).strip("_")

        # Fallback to column_{index} if cleaning resulted in an empty string
        if not name:
            name = f"column_{index}"

        # Prepend 'column_' prefix if sanitized column name starts with a number (invalid SQL identifier)
        if name[0].isdigit():
            name = f"column_{name}"

        # Initialize suffix search variables to handle duplicate column names
        candidate = name
        suffix = 2

        # Append incrementing numeric suffixes (_2, _3, etc.) until a unique identifier is found
        while candidate in used:
            candidate = f"{name}_{suffix}"
            suffix += 1

        # Register candidate in used set and append to result list
        used.add(candidate)
        result.append(candidate)

    # Return final list of unique SQL-safe column names
    return result


def _content_fingerprint(dataframe: pd.DataFrame) -> str:
    """Fingerprint normalized spreadsheet headers and complete row values."""
    rows = []
    for row in dataframe.itertuples(index=False, name=None):
        normalized_row = []
        for value in row:
            if pd.isna(value):
                normalized_row.append(None)
            elif hasattr(value, "item"):
                normalized_row.append(value.item())
            elif hasattr(value, "isoformat"):
                normalized_row.append(value.isoformat())
            else:
                normalized_row.append(value)
        rows.append(normalized_row)

    canonical_content = json.dumps(
        {"columns": list(dataframe.columns), "rows": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()


# Helper function to initialize required database tables and indexes if they do not exist
def _create_metadata_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_id TEXT PRIMARY KEY,
            original_filename TEXT NOT NULL,
            table_name TEXT NOT NULL UNIQUE,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            upload_status TEXT NOT NULL DEFAULT 'complete',
            parsing_status TEXT NOT NULL DEFAULT 'complete',
            storage_status TEXT NOT NULL DEFAULT 'complete',
            chunk_status TEXT NOT NULL DEFAULT 'complete',
            graph_build_status TEXT NOT NULL DEFAULT 'pending',
            graph_updated_at TEXT,
            graph_error TEXT
        )
        """
    )
    existing = {
        row[1]
        for row in connection.execute('PRAGMA table_info("datasets")')
    }
    for column_name, default_value in DATASET_STATUS_COLUMNS.items():
        if column_name in existing:
            continue
        if default_value is None:
            connection.execute(
                f'ALTER TABLE datasets ADD COLUMN {column_name} TEXT'
            )
        else:
            connection.execute(
                f"ALTER TABLE datasets ADD COLUMN {column_name} TEXT NOT NULL DEFAULT '{default_value}'"
            )

    # Create 'dataset_columns' table mapping original column names to generated SQL column names
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_columns (
            dataset_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            sql_name TEXT NOT NULL,
            data_type TEXT NOT NULL,
            null_count INTEGER NOT NULL,
            PRIMARY KEY (dataset_id, ordinal),
            FOREIGN KEY (dataset_id)
                REFERENCES datasets(dataset_id)
                ON DELETE CASCADE
        )
        """
    )

    # Create 'document_chunks' table storing searchable text chunks and JSON metadata
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (dataset_id, chunk_index),
            FOREIGN KEY (dataset_id)
                REFERENCES datasets(dataset_id)
                ON DELETE CASCADE
        )
        """
    )

    # Create composite index on dataset_id and chunk_index to optimize chunk retrieval queries
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_chunks_dataset
        ON document_chunks(dataset_id, chunk_index)
        """
    )


# Main function to store dataset table data, metadata, and optional chunks in SQLite
def store_spreadsheet(
    file_path: str | Path,
    original_filename: str,
    database_path: str | Path = "data/graphsheet.db",
    chunks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Store spreadsheet rows, schema metadata, and optional text chunks."""

    # Convert file_path input parameter to a pathlib Path object
    path = Path(file_path)
    # Extract lowercased extension from original filename
    extension = Path(original_filename).suffix.lower()

    # Reject unsupported file extension types
    if extension not in SUPPORTED_EXTENSIONS:
        raise DatasetStorageError(
            "Unsupported file type. Upload a .csv, .xls, or .xlsx file."
        )

    # Ensure source file exists on disk and is a standard file
    if not path.exists() or not path.is_file():
        raise DatasetStorageError("The uploaded file could not be found.")

    # Reject empty files containing zero bytes
    if path.stat().st_size == 0:
        raise DatasetStorageError("The uploaded file is empty.")

    # Create database parent directory path if missing
    database = Path(database_path)
    database.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Load file contents into pandas DataFrame
        dataframe = _read_dataframe(path, extension)
    except Exception as exc:
        raise DatasetStorageError(
            f"Could not read spreadsheet: {exc}"
        ) from exc

    # Sanitize dataframe column headers into SQL-safe names
    original_columns = [str(column) for column in dataframe.columns]
    sql_columns = _safe_column_names(original_columns)
    dataframe.columns = sql_columns
    content_fingerprint = _content_fingerprint(dataframe)

    # Initialize empty list to collect validated chunk objects before database insertion
    validated_chunks: list[dict[str, Any]] = []

    # Validate incoming chunks if provided
    if chunks is not None:
        # Verify chunks parameter is a list structure
        if not isinstance(chunks, list):
            raise DatasetStorageError("Chunks must be supplied as a list.")

        # Loop through each chunk item and validate expected structure and content
        for index, chunk in enumerate(chunks):
            # Check that each chunk item is a valid dictionary
            if not isinstance(chunk, dict):
                raise DatasetStorageError(
                    f"Chunk at index {index} must be a dictionary."
                )

            # Ensure chunk text key is present, is a string, and contains non-whitespace text
            chunk_text = chunk.get("text")
            if not isinstance(chunk_text, str) or not chunk_text.strip():
                raise DatasetStorageError(
                    f"Chunk at index {index} must contain non-empty text."
                )

            # Extract chunk metadata and ensure it is a dictionary structure
            metadata = chunk.get("metadata", {})
            if not isinstance(metadata, dict):
                raise DatasetStorageError(
                    f"Chunk metadata at index {index} must be a dictionary."
                )

            # Attempt JSON serialization of chunk metadata payload
            try:
                metadata_json = json.dumps(
                    metadata,
                    ensure_ascii=False,
                    default=str,
                )
            except (TypeError, ValueError) as exc:
                raise DatasetStorageError(
                    f"Could not serialize metadata for chunk {index}: {exc}"
                ) from exc

            # Append fully validated chunk record object
            validated_chunks.append(
                {
                    "chunk_id": str(chunk.get("chunk_id") or uuid.uuid4().hex),
                    "chunk_index": index,
                    "text": chunk_text,
                    "metadata_json": metadata_json,
                }
            )

    # Generate unique 32-character hex ID for dataset and dynamically construct target table name
    dataset_id = uuid.uuid4().hex
    table_name = f"dataset_{dataset_id}"
    # Generate ISO-8601 UTC timestamp string for creation tracking
    created_at = datetime.now(timezone.utc).isoformat()

    # Initialize connection handle object for explicit exception handling
    connection = None

    try:
        # Connect to SQLite database file
        connection = sqlite3.connect(database)
        # Enable SQLite foreign key constraints enforcing CASCADE actions
        connection.execute("PRAGMA foreign_keys = ON")
        # Ensure metadata schema tables exist
        _create_metadata_tables(connection)

        # Begin explicit SQL database transaction
        connection.execute("BEGIN")

        # Write dataset DataFrame rows into dedicated SQLite table dynamically
        dataframe.to_sql(
            table_name,
            connection,
            if_exists="fail",  # Prevent overwriting existing tables
            index=False,      # Exclude pandas row index column
        )

        # Insert dataset summary record into main 'datasets' table
        connection.execute(
            """
            INSERT INTO datasets (
                dataset_id, original_filename, table_name,
                row_count, column_count, created_at,
                upload_status, parsing_status, storage_status,
                chunk_status, graph_build_status, graph_updated_at,
                graph_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset_id,
                Path(original_filename).name,
                table_name,
                len(dataframe),
                len(sql_columns),
                created_at,
                "complete",
                "complete",
                "complete",
                "complete",
                "pending",
                None,
                None,
            ),
        )

        # Insert column schema details for each column into 'dataset_columns' table
        for ordinal, (original, sql_name) in enumerate(
            zip(original_columns, sql_columns)
        ):
            connection.execute(
                """
                INSERT INTO dataset_columns (
                    dataset_id, ordinal, original_name,
                    sql_name, data_type, null_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    ordinal,
                    original,
                    sql_name,
                    str(dataframe[sql_name].dtype),
                    int(dataframe[sql_name].isna().sum()),
                ),
            )

        # Insert each validated document chunk record into 'document_chunks' table
        for chunk in validated_chunks:
            connection.execute(
                """
                INSERT INTO document_chunks (
                    chunk_id, dataset_id, chunk_index,
                    text, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["chunk_id"],
                    dataset_id,
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk["metadata_json"],
                    created_at,
                ),
            )

        # Commit transaction writing all updates atomically to database
        connection.commit()

    except Exception as exc:
        # Roll back transaction if any error occurs during processing or insertion
        if connection is not None:
            connection.rollback()

        raise DatasetStorageError(
            f"Failed to store dataset in SQLite: {exc}"
        ) from exc

    finally:
        # Ensure database connection is closed safely regardless of success or failure
        if connection is not None:
            connection.close()

    # Return dict containing dataset summary details and column mappings
    return {
        "dataset_id": dataset_id,
        "filename": Path(original_filename).name,
        "table_name": table_name,
        "content_fingerprint": content_fingerprint,
        "row_count": int(len(dataframe)),
        "column_count": len(sql_columns),
        "chunk_count": len(validated_chunks),
        "columns": [
            {
                "original_name": original,
                "sql_name": sql_name,
                "data_type": str(dataframe[sql_name].dtype),
                "null_count": int(dataframe[sql_name].isna().sum()),
            }
            for original, sql_name in zip(original_columns, sql_columns)
        ],
    }


# Function to query and retrieve stored document chunks for a specific dataset ID
def get_dataset_chunks(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> list[dict[str, Any]]:
    """Retrieve persisted text chunks for a dataset in their original order."""

    database = Path(database_path)

    # Verify that the database file exists on disk
    if not database.exists():
        raise DatasetStorageError("The SQLite database could not be found.")

    try:
        # Open SQLite connection using context manager
        with sqlite3.connect(database) as connection:
            # Set row_factory to sqlite3.Row to access query columns by name
            connection.row_factory = sqlite3.Row
            # Execute SQL query fetching chunk records sorted by chunk_index
            rows = connection.execute(
                """
                SELECT chunk_id, dataset_id, chunk_index, text, metadata_json
                FROM document_chunks
                WHERE dataset_id = ?
                ORDER BY chunk_index
                """,
                (dataset_id,),
            ).fetchall()

        # Reconstruct list of chunk dictionaries, parsing JSON metadata strings back into Python dicts
        return [
            {
                "chunk_id": row["chunk_id"],
                "dataset_id": row["dataset_id"],
                "chunk_index": row["chunk_index"],
                "text": row["text"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    # Catch database execution or JSON parsing errors and raise custom exception
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise DatasetStorageError(
            f"Could not retrieve dataset chunks: {exc}"
        ) from exc







def get_dataset_details(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> dict[str, Any] | None:
    """Retrieve metadata for one dataset by its ID."""

    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("Dataset ID must not be empty.")

    database = Path(database_path)

    if not database.is_file():
        raise DatasetStorageError("The SQLite database could not be found.")

    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at,
                           upload_status, parsing_status, storage_status,
                           chunk_status, graph_build_status, graph_updated_at,
                           graph_error
                    FROM datasets
                    WHERE dataset_id = ?
                    """,
                    (dataset_id.strip(),),
                ).fetchone()
            except sqlite3.OperationalError:
                row = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at
                    FROM datasets
                    WHERE dataset_id = ?
                    """,
                    (dataset_id.strip(),),
                ).fetchone()

        if row is None:
            return None
        metadata = dict(row)
        metadata.setdefault("upload_status", "complete")
        metadata.setdefault("parsing_status", "complete")
        metadata.setdefault("storage_status", "complete")
        metadata.setdefault("chunk_status", "complete")
        metadata.setdefault("graph_build_status", "pending")
        metadata.setdefault("graph_updated_at", None)
        metadata.setdefault("graph_error", None)
        metadata["graph_status"] = metadata.get("graph_build_status", "pending")
        metadata["graph_available"] = metadata.get("graph_build_status") == "complete"
        return metadata

    except sqlite3.Error as exc:
        raise DatasetStorageError(
            f"Could not retrieve dataset details: {exc}"
        ) from exc


def delete_spreadsheet(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> bool:
    """Delete a dataset after its persisted graph records are cleared."""

    if (
        not isinstance(dataset_id, str)
        or len(dataset_id) != 32
        or any(character not in "0123456789abcdef" for character in dataset_id)
    ):
        raise ValueError("Dataset ID is invalid.")

    database = Path(database_path)
    if not database.is_file():
        return False

    try:
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            dataset = connection.execute(
                "SELECT table_name FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            ).fetchone()
            if dataset is None:
                return False
            if dataset["table_name"] != f"dataset_{dataset_id}":
                raise DatasetStorageError("Stored dataset metadata is invalid.")
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            conversation_ids = _dataset_conversation_ids(
                connection,
                existing_tables,
                dataset_id,
            )
        try:
            delete_dataset_graph(
                dataset_id,
                database_url=graph_database_url_for_source(database),
            )
        except GraphPersistenceError as exc:
            raise DatasetStorageError(
                "Could not remove the dataset's persistent knowledge graph."
            ) from exc

        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.row_factory = sqlite3.Row

            dataset = connection.execute(
                """
                SELECT table_name
                FROM datasets
                WHERE dataset_id = ?
                """,
                (dataset_id,),
            ).fetchone()

            if dataset is None:
                return False

            table_name = dataset["table_name"]
            if table_name != f"dataset_{dataset_id}":
                raise DatasetStorageError(
                    "Stored dataset metadata is invalid."
                )

            current_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            current_conversation_ids = _dataset_conversation_ids(
                connection,
                current_tables,
                dataset_id,
            )
            if current_conversation_ids != conversation_ids:
                raise DatasetStorageError(
                    "Dataset conversation references changed during deletion. "
                    "Retry the operation."
                )

            for conversation_id in conversation_ids:
                if "conversation_events" in current_tables:
                    connection.execute(
                        """
                        DELETE FROM conversation_events
                        WHERE conversation_id = ?
                        """,
                        (conversation_id,),
                    )
                if "conversations" in current_tables:
                    connection.execute(
                        """
                        DELETE FROM conversations
                        WHERE conversation_id = ?
                        """,
                        (conversation_id,),
                    )

            quoted_table = '"' + table_name.replace('"', '""') + '"'
            connection.execute(f"DROP TABLE IF EXISTS {quoted_table}")
            if "document_chunks" in current_tables:
                connection.execute(
                    "DELETE FROM document_chunks WHERE dataset_id = ?",
                    (dataset_id,),
                )
            if "dataset_columns" in current_tables:
                connection.execute(
                    "DELETE FROM dataset_columns WHERE dataset_id = ?",
                    (dataset_id,),
                )
            connection.execute(
                "DELETE FROM datasets WHERE dataset_id = ?",
                (dataset_id,),
            )

        return True
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        raise DatasetStorageError(
            "Could not delete the dataset and its associated data."
        ) from exc


def _dataset_conversation_ids(
    connection: sqlite3.Connection,
    existing_tables: set[str],
    dataset_id: str,
) -> set[str]:
    """Find every conversation whose persisted state references a dataset."""
    conversation_ids: set[str] = set()
    if "conversations" in existing_tables:
        conversation_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)")
        }
        if "selected_dataset_ids" in conversation_columns:
            for row in connection.execute(
                """
                SELECT conversation_id, dataset_id, selected_dataset_ids
                FROM conversations
                """
            ):
                selected_ids = json.loads(row["selected_dataset_ids"] or "[]")
                if row["dataset_id"] == dataset_id or dataset_id in selected_ids:
                    conversation_ids.add(row["conversation_id"])
        else:
            conversation_ids.update(
                row["conversation_id"]
                for row in connection.execute(
                    "SELECT conversation_id FROM conversations WHERE dataset_id = ?",
                    (dataset_id,),
                )
            )

    if "conversation_events" in existing_tables:
        for event in connection.execute(
            """
            SELECT conversation_id, metadata_json
            FROM conversation_events
            WHERE metadata_json IS NOT NULL
            """
        ):
            metadata = json.loads(event["metadata_json"])
            if isinstance(metadata, dict) and (
                metadata.get("dataset_id") == dataset_id
                or dataset_id in metadata.get("dataset_ids", [])
            ):
                conversation_ids.add(event["conversation_id"])
    return conversation_ids
