
"""Infer possible relationships between stored spreadsheet datasets."""

import hashlib
import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


class RelationshipInferenceError(Exception):
    """Raised when relationship inference cannot be completed."""


MAX_DISTINCT_VALUES = 5000
MIN_SHARED_VALUES = 2
MIN_NAME_SIMILARITY = 0.60
MIN_OVERLAP_RATIO = 0.50


def _quote_identifier(identifier: str) -> str:
    """Safely quote a SQLite table or column identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_name(name: str) -> str:
    """Normalize column names for comparison."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def _normalize_value(value: Any) -> str:
    """Normalize values for basic cross-dataset comparison."""
    return str(value).strip().casefold()


def _column_values(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> tuple[set[str], bool]:
    """Read distinct non-empty values, with a safety limit."""
    table = _quote_identifier(table_name)
    column = _quote_identifier(column_name)

    rows = connection.execute(
        f"""
        SELECT DISTINCT {column}
        FROM {table}
        WHERE {column} IS NOT NULL
          AND TRIM(CAST({column} AS TEXT)) != ''
        LIMIT ?
        """,
        (MAX_DISTINCT_VALUES + 1,),
    ).fetchall()

    truncated = len(rows) > MAX_DISTINCT_VALUES
    values = {
        _normalize_value(row[0])
        for row in rows[:MAX_DISTINCT_VALUES]
    }

    return values, truncated


def _table_fingerprint(
    connection: sqlite3.Connection,
    table_name: str,
) -> str:
    """Return a deterministic fingerprint of a table's schema and contents.

    Row order is ignored, but duplicate rows are preserved. Values are
    serialized with their SQLite types so different typed values are not
    accidentally treated as identical.
    """
    table = _quote_identifier(table_name)

    schema = connection.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    columns = [row["name"] for row in schema]

    digest = hashlib.sha256()
    digest.update(
        json.dumps(columns, ensure_ascii=False).encode("utf-8")
    )

    if not columns:
        return digest.hexdigest()

    quoted_columns = ", ".join(
        _quote_identifier(column) for column in columns
    )

    rows = connection.execute(
        f"SELECT {quoted_columns} FROM {table}"
    ).fetchall()

    # Sort serialized rows so that different insertion orders produce
    # the same fingerprint. Duplicate rows remain in the list.
    serialized_rows = [
        json.dumps(
            [
                [type(value).__name__, value]
                for value in tuple(row)
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        for row in rows
    ]

    for serialized_row in sorted(serialized_rows):
        digest.update(b"\n")
        digest.update(serialized_row.encode("utf-8"))

    return digest.hexdigest()


def infer_relationships(
    database_path: str | Path = "data/graphsheet.db",
    dataset_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Suggest candidate relationships based on names and shared values.

    Confidence is a heuristic score, not a probability or proof of a
    genuine business relationship. Identical dataset copies are excluded
    from cross-dataset comparisons to prevent redundant relationships.
    """
    database_path = Path(database_path)

    if not database_path.is_file():
        raise RelationshipInferenceError(
            f"Database not found: {database_path}"
        )

    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row

            metadata_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(datasets)"
                ).fetchall()
            }

            if "original_filename" in metadata_columns:
                filename_column = "original_filename"
            elif "filename" in metadata_columns:
                filename_column = "filename"
            else:
                raise RelationshipInferenceError(
                    "The datasets table must contain either "
                    "'original_filename' or 'filename'."
                )

            query = f"""
                SELECT
                    dataset_id,
                    {_quote_identifier(filename_column)} AS filename,
                    table_name
                FROM datasets
            """
            parameters: tuple[str, ...] = ()
            if dataset_ids is not None:
                if not dataset_ids:
                    return {
                        "dataset_count": 0,
                        "relationship_count": 0,
                        "relationships": [],
                    }
                placeholders = ", ".join("?" for _ in dataset_ids)
                query += f" WHERE dataset_id IN ({placeholders})"
                parameters = tuple(dataset_ids)
            query += " ORDER BY dataset_id"
            datasets = connection.execute(query, parameters).fetchall()

            dataset_columns: list[dict[str, Any]] = []
            dataset_fingerprints: dict[str, str] = {}

            for dataset in datasets:
                table_name = dataset["table_name"]

                exists = connection.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type = 'table' AND name = ?
                    """,
                    (table_name,),
                ).fetchone()

                if (
                    exists is None
                    or not table_name.startswith("dataset_")
                ):
                    continue

                dataset_id = dataset["dataset_id"]

                # Compute one content fingerprint per dataset table.
                dataset_fingerprints[dataset_id] = (
                    _table_fingerprint(connection, table_name)
                )

                table = _quote_identifier(table_name)
                schema = connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()

                for column in schema:
                    column_name = column["name"]

                    values, truncated = _column_values(
                        connection,
                        table_name,
                        column_name,
                    )

                    dataset_columns.append(
                        {
                            "dataset_id": dataset_id,
                            "filename": dataset["filename"],
                            "table_name": table_name,
                            "column_name": column_name,
                            "normalized_name": _normalize_name(
                                column_name
                            ),
                            "values": values,
                            "truncated": truncated,
                        }
                    )

            relationships: list[dict[str, Any]] = []

            for index, left in enumerate(dataset_columns):
                for right in dataset_columns[index + 1:]:
                    left_id = left["dataset_id"]
                    right_id = right["dataset_id"]

                    # Never infer relationships between columns belonging
                    # to the same dataset.
                    if left_id == right_id:
                        continue

                    # Skip different dataset IDs when their complete
                    # table contents and schemas are identical.
                    if (
                        left_id in dataset_fingerprints
                        and right_id in dataset_fingerprints
                        and dataset_fingerprints[left_id]
                        == dataset_fingerprints[right_id]
                    ):
                        continue

                    # Skip empty or truncated samples.
                    if (
                        not left["values"]
                        or not right["values"]
                        or left["truncated"]
                        or right["truncated"]
                    ):
                        continue

                    left_name = left["normalized_name"]
                    right_name = right["normalized_name"]

                    name_similarity = SequenceMatcher(
                        None,
                        left_name,
                        right_name,
                    ).ratio()

                    # Allow common key-name variations, including
                    # student_id, studentId, student_id_fk, and id_student.
                    names_match = (
                        name_similarity >= MIN_NAME_SIMILARITY
                        or (
                            left_name
                            and right_name
                            and (
                                left_name in right_name
                                or right_name in left_name
                            )
                        )
                    )

                    if not names_match:
                        continue

                    shared_values = left["values"] & right["values"]

                    if len(shared_values) < MIN_SHARED_VALUES:
                        continue

                    overlap_ratio = (
                        len(shared_values)
                        / min(
                            len(left["values"]),
                            len(right["values"]),
                        )
                    )

                    if overlap_ratio < MIN_OVERLAP_RATIO:
                        continue

                    confidence = round(
                        0.45 * name_similarity
                        + 0.55 * overlap_ratio,
                        3,
                    )

                    relationships.append(
                        {
                            "left": {
                                "dataset_id": left_id,
                                "filename": left["filename"],
                                "table_name": left["table_name"],
                                "column": left["column_name"],
                            },
                            "right": {
                                "dataset_id": right_id,
                                "filename": right["filename"],
                                "table_name": right["table_name"],
                                "column": right["column_name"],
                            },
                            "shared_value_count": len(shared_values),
                            "name_similarity": round(
                                name_similarity, 3
                            ),
                            "overlap_ratio": round(
                                overlap_ratio, 3
                            ),
                            "confidence": confidence,
                            "status": "candidate",
                            "evidence": (
                                "Similar column names and overlapping "
                                "non-empty values."
                            ),
                        }
                    )

            relationships.sort(
                key=lambda item: item["confidence"],
                reverse=True,
            )

            return {
                "dataset_count": len(datasets),
                "relationship_count": len(relationships),
                "relationships": relationships,
            }

    except sqlite3.Error as exc:
        raise RelationshipInferenceError(
            f"Could not infer dataset relationships: {exc}"
        ) from exc
