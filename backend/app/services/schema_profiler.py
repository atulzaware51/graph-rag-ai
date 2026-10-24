"""Profile the schema and data quality of a stored dataset."""

# Import necessary standard libraries
import sqlite3
from pathlib import Path
from typing import Any

# Import custom exceptions and helper functions from the backend application
from backend.app.services.dataset_reader import (
    DatasetNotFoundError,
    get_dataset_details,
)


class SchemaProfilerError(Exception):
    """Raised when a dataset cannot be profiled."""
    # Custom exception class to handle errors specific to schema profiling


def profile_dataset(
    dataset_id: str,
    database_path: str | Path = "data/graphsheet.db",
) -> dict[str, Any]:
    """Return column statistics and potential key information."""

    # Convert the provided database path string into a Path object for easier file operations
    database_path = Path(database_path)

    # Check if the database file actually exists on the filesystem
    if not database_path.is_file():
        raise DatasetNotFoundError(
            f"Database not found: {database_path}"
        )

    try:
        # Fetch metadata about the dataset using its ID
        dataset = get_dataset_details(
            dataset_id,
            database_path=database_path,
        )

        # Extract the table name associated with this dataset from the metadata
        table_name = dataset.get("table_name")
        if not table_name:
            raise SchemaProfilerError(
                "Dataset metadata does not contain a table name."
            )

        # Validate the table name against the dataset metadata.
        # Open a context-managed connection to the SQLite database
        with sqlite3.connect(database_path) as connection:
            # Configure the connection to return rows as dictionaries (accessible by column name)
            connection.row_factory = sqlite3.Row

            # Query SQLite's internal master table to verify our target table exists
            metadata_row = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()

            # Ensure the table exists and enforces a naming convention (must start with "dataset_")
            if metadata_row is None or not table_name.startswith(
                "dataset_"
            ):
                raise SchemaProfilerError(
                    "The dataset table is missing or invalid."
                )

            # Quote the validated identifier safely to prevent SQL injection or syntax errors 
            # if the table name contains spaces or special characters
            quoted_table = '"' + table_name.replace('"', '""') + '"'

            # Fetch the schema information (columns, data types, etc.) for the table
            table_info = connection.execute(
                f"PRAGMA table_info({quoted_table})"
            ).fetchall()

            # Get the total number of rows in the table
            row_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_table}"
            ).fetchone()[0]

            # Initialize a list to hold profiling data for each column
            columns = []

            # Iterate over every column found in the table schema
            for info in table_info:
                column_name = info["name"]
                # Safely quote the column name for use in queries
                quoted_column = '"' + column_name.replace('"', '""') + '"'

                # Execute a query to get basic statistics for the current column
                stats = connection.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total_count,
                        COUNT({quoted_column}) AS non_null_count,
                        COUNT(DISTINCT {quoted_column}) AS unique_count
                    FROM {quoted_table}
                    """
                ).fetchone()

                # Fetch a small sample of distinct, non-null values from the column
                samples = connection.execute(
                    f"""
                    SELECT DISTINCT {quoted_column}
                    FROM {quoted_table}
                    WHERE {quoted_column} IS NOT NULL
                    LIMIT 5
                    """
                ).fetchall()

                # Extract the statistics into local variables
                total_count = stats["total_count"]
                non_null_count = stats["non_null_count"]
                unique_count = stats["unique_count"]
                # Calculate how many rows have a NULL value in this column
                null_count = total_count - non_null_count

                # A candidate key must be non-null and unique.
                # Determine if this column could serve as a primary key for the table
                is_potential_key = (
                    total_count > 0
                    and null_count == 0
                    and unique_count == total_count
                )

                # Append the profiled data for this column to our list
                columns.append(
                    {
                        "name": column_name,
                        "sqlite_type": info["type"] or "UNKNOWN", # Fallback to UNKNOWN if type is missing
                        "null_count": null_count,
                        "unique_count": unique_count,
                        "sample_values": [
                            row[0] for row in samples # Unpack the single-item tuples into a flat list
                        ],
                        "is_potential_key": is_potential_key,
                    }
                )

        # Return the final aggregated profile of the dataset and its columns
        return {
            "dataset_id": dataset_id,
            "filename": dataset.get("filename"),
            "table_name": table_name,
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
        }

    # Catch custom domain errors and re-raise them as-is
    except (DatasetNotFoundError, SchemaProfilerError):
        raise
    # Catch raw SQLite database errors, wrap them in our custom domain error, and raise
    except sqlite3.Error as exc:
        raise SchemaProfilerError(
            f"SQLite error while profiling dataset "
            f"{dataset_id!r}: {exc}"
        ) from exc