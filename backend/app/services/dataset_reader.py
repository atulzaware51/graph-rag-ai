# Import sqlite3 module to interact with SQLite databases
import sqlite3
# Import Path class from pathlib to work with file paths across different operating systems
from pathlib import Path
# Import Any type hint to represent generic dictionary value types
from typing import Any


# Define the default path where the SQLite database file is located
DEFAULT_DATABASE_PATH = Path("data/graphsheet.db")


# Custom exception raised when a requested dataset ID does not exist in the database
class DatasetNotFoundError(Exception):
    """Raised when a requested dataset does not exist."""


# Custom exception raised when a general database read or query execution error occurs
class DatasetReaderError(Exception):
    """Raised when dataset metadata cannot be retrieved."""


# Public function to retrieve summary metadata for all stored datasets
def list_datasets(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return metadata for every stored dataset."""
    # Convert input path to a Path object
    database = Path(database_path)

    # Return an empty list early if the database file does not exist on disk
    if not database.is_file():
        return []

    # Attempt to open a database connection and query dataset records
    try:
        # Establish SQLite connection using context manager for automatic closure
        with sqlite3.connect(database) as connection:
            # Configure rows to be returned as dictionary-accessible Row objects
            connection.row_factory = sqlite3.Row

            # Query all datasets from the database sorted by creation timestamp (newest first)
            try:
                rows = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at,
                           upload_status, parsing_status, storage_status,
                           chunk_status, graph_build_status, graph_updated_at,
                           graph_error
                    FROM datasets
                    ORDER BY created_at DESC
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                rows = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at
                    FROM datasets
                    ORDER BY created_at DESC
                    """
                ).fetchall()

            datasets = []
            for row in rows:
                payload = dict(row)
                payload.setdefault("upload_status", "complete")
                payload.setdefault("parsing_status", "complete")
                payload.setdefault("storage_status", "complete")
                payload.setdefault("chunk_status", "complete")
                payload.setdefault("graph_build_status", "pending")
                payload.setdefault("graph_updated_at", None)
                payload.setdefault("graph_error", None)
                payload["graph_status"] = payload.get("graph_build_status", "pending")
                payload["graph_available"] = payload.get("graph_build_status") == "complete"
                datasets.append(payload)
            return datasets

    # Catch SQLite runtime errors and wrap them in a custom exception
    except sqlite3.Error as exc:
        raise DatasetReaderError("Could not list datasets.") from exc


# Public function to retrieve full details and column schema for a specific dataset ID
def get_dataset_details(
    dataset_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Return one dataset and its column metadata."""
    # Convert input path to a Path object
    database = Path(database_path)

    # Raise DatasetNotFoundError early if the database file does not exist
    if not database.is_file():
        raise DatasetNotFoundError(
            f"Dataset '{dataset_id}' was not found."
        )

    # Attempt to fetch dataset details and column metadata from SQLite
    try:
        # Establish SQLite connection using context manager for automatic closure
        with sqlite3.connect(database) as connection:
            # Configure rows to be returned as dictionary-accessible Row objects
            connection.row_factory = sqlite3.Row

            # Query primary metadata for the specified dataset ID
            try:
                dataset = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at,
                           upload_status, parsing_status, storage_status,
                           chunk_status, graph_build_status, graph_updated_at,
                           graph_error
                    FROM datasets
                    WHERE dataset_id = ?
                    """,
                    (dataset_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                dataset = connection.execute(
                    """
                    SELECT dataset_id, original_filename, table_name,
                           row_count, column_count, created_at
                    FROM datasets
                    WHERE dataset_id = ?
                    """,
                    (dataset_id,),
                ).fetchone()

            # Check if matching dataset record was found
            if dataset is None:
                # Raise error if dataset ID does not exist in the database
                raise DatasetNotFoundError(
                    f"Dataset '{dataset_id}' was not found."
                )

            # Query column schema details associated with the dataset ID, ordered by column index
            columns = connection.execute(
                """
                SELECT ordinal, original_name, sql_name,
                       data_type, null_count
                FROM dataset_columns
                WHERE dataset_id = ?
                ORDER BY ordinal
                """,
                (dataset_id,),
            ).fetchall()

        # Convert main dataset row to a standard dictionary
        result = dict(dataset)
        result.setdefault("upload_status", "complete")
        result.setdefault("parsing_status", "complete")
        result.setdefault("storage_status", "complete")
        result.setdefault("chunk_status", "complete")
        result.setdefault("graph_build_status", "pending")
        result.setdefault("graph_updated_at", None)
        result.setdefault("graph_error", None)
        result["graph_status"] = result.get("graph_build_status", "pending")
        result["graph_available"] = result.get("graph_build_status") == "complete"
        # Convert column rows to dictionaries and attach as a list under 'columns'
        result["columns"] = [dict(column) for column in columns]
        # Return complete dataset dictionary with its column schema
        return result

    # Allow DatasetNotFoundError to pass through without wrapping
    except DatasetNotFoundError:
        raise
    # Catch any underlying SQLite errors and re-raise as custom DatasetReaderError
    except sqlite3.Error as exc:
        raise DatasetReaderError("Could not retrieve dataset details.") from exc


def get_dataset_preview(
    dataset_id: str,
    limit: int = 10,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Return a bounded sample of rows from one registered dataset."""

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("Preview limit must be between 1 and 100.")

    dataset = get_dataset_details(dataset_id, database_path)
    table_name = dataset["table_name"]
    if (
        not isinstance(table_name, str)
        or table_name != f"dataset_{dataset_id}"
        or len(dataset_id) != 32
        or any(character not in "0123456789abcdef" for character in dataset_id)
    ):
        raise DatasetReaderError("Dataset metadata is invalid.")

    database = Path(database_path)
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        try:
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            rows = connection.execute(
                f"SELECT * FROM {quoted_table} LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise DatasetReaderError(
            "Could not read the dataset preview."
        ) from exc