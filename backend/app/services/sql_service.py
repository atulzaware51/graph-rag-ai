"""Safe, read-only SQL execution restricted to one dataset."""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "graphsheet.db"
MAX_QUERY_LENGTH = 10_000
MAX_RESULT_ROWS = 500
MAX_RESULT_BYTES = 1_000_000
QUERY_TIMEOUT_SECONDS = 5

_SAFE_SQL_FUNCTIONS = {
    "abs", "avg", "char", "coalesce", "count", "date", "datetime",
    "glob", "group_concat", "hex", "ifnull", "instr", "julianday",
    "length", "like", "likelihood", "likely", "lower", "ltrim",
    "max", "min", "nullif", "replace", "round", "rtrim", "substr",
    "sum", "time", "total", "trim", "typeof", "unicode", "unlikely",
    "unixepoch", "upper",
}


class SQLServiceError(RuntimeError):
    """Raised when a SQL query cannot be safely executed."""


def execute_readonly_sql(
    query: str,
    database_path: str | Path = DEFAULT_DATABASE,
    allowed_table: str | None = None,
) -> dict[str, Any]:
    """Execute one bounded read-only SELECT, optionally restricted to one table."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("SQL query must not be empty.")

    sql = query.strip()
    if len(sql) > MAX_QUERY_LENGTH:
        raise SQLServiceError("SQL query exceeds the maximum allowed length.")

    if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE):
        raise SQLServiceError("Only SELECT or WITH queries are allowed.")

    dataset_id = None
    if allowed_table is not None:
        match = re.fullmatch(r"dataset_([a-fA-F0-9]{32})", allowed_table)
        if match is None:
            raise SQLServiceError("Invalid dataset table name.")
        dataset_id = match.group(1)

    database = Path(database_path)
    if not database.is_file():
        raise SQLServiceError("Database file does not exist.")

    connection: sqlite3.Connection | None = None
    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    timed_out = False

    def interrupt_if_expired() -> int:
        nonlocal timed_out
        if time.monotonic() >= deadline:
            timed_out = True
            return 1
        return 0

    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row

        if allowed_table is not None:
            registered = connection.execute(
                """
                SELECT 1
                FROM datasets
                WHERE dataset_id = ? AND table_name = ?
                """,
                (dataset_id, allowed_table),
            ).fetchone()
            if registered is None:
                raise SQLServiceError(
                    "Dataset table was not found in the dataset registry."
                )

        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
        }
        read_columns: list[str] = []
        recursive_action = getattr(sqlite3, "SQLITE_RECURSIVE", None)
        if recursive_action is not None:
            allowed_actions.add(recursive_action)

        def authorize(
            action: int,
            arg1: str | None,
            arg2: str | None,
            database_name: str | None,
            _trigger_name: str | None,
        ) -> int:
            if action not in allowed_actions:
                return sqlite3.SQLITE_DENY

            if action == sqlite3.SQLITE_FUNCTION:
                function_name = (arg2 or arg1 or "").lower()
                return (
                    sqlite3.SQLITE_OK
                    if function_name in _SAFE_SQL_FUNCTIONS
                    else sqlite3.SQLITE_DENY
                )

            if (
                action == sqlite3.SQLITE_READ
                and allowed_table is not None
                and (
                    arg1 != allowed_table
                    or database_name != "main"
                )
            ):
                return sqlite3.SQLITE_DENY

            if (
                action == sqlite3.SQLITE_READ
                and arg2
                and arg2 not in read_columns
            ):
                read_columns.append(arg2)

            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        connection.set_progress_handler(interrupt_if_expired, 1_000)
        cursor = connection.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        rows = [dict(row) for row in cursor.fetchmany(MAX_RESULT_ROWS + 1)]

        if timed_out:
            raise SQLServiceError("SQL query exceeded the execution time limit.")
        if len(rows) > MAX_RESULT_ROWS:
            raise SQLServiceError(
                f"Query returned too many rows. Add a LIMIT of "
                f"{MAX_RESULT_ROWS} or less."
            )

        try:
            result_size = len(
                json.dumps(
                    rows,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise SQLServiceError(
                "Query returned values that cannot be represented safely."
            ) from exc

        if result_size > MAX_RESULT_BYTES:
            raise SQLServiceError(
                "Query result exceeds the maximum allowed size."
            )

        return {
            "status": "success",
            "columns": columns,
            "row_count": len(rows),
            "rows": rows,
            "read_columns": read_columns,
        }

    except SQLServiceError:
        raise
    except sqlite3.Error as exc:
        if timed_out:
            raise SQLServiceError(
                "SQL query exceeded the execution time limit."
            ) from None
        raise SQLServiceError(
            "SQL execution failed or the query was not authorized."
        ) from exc
    finally:
        if connection is not None:
            connection.close()
