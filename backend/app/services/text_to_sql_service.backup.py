
"""Gemini-powered natural-language to SQL generation."""

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai

from backend.app.services.dataset_store import (
    DatasetStorageError,
    get_dataset_details,
)
from backend.app.services.sql_service import (
    DEFAULT_DATABASE,
    SQLServiceError,
    execute_readonly_sql,
)


# Resolve the project root and load environment variables from .env.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


class TextToSQLError(RuntimeError):
    """Raised when natural-language SQL generation fails."""


def ask_with_sql(
    question: str,
    dataset_id: str,
) -> dict[str, Any]:
    """Generate and execute a read-only SQL query for one dataset."""

    # 1. Validate the user's input.
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must not be empty.")

    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise ValueError("Dataset ID must not be empty.")

    question = question.strip()
    dataset_id = dataset_id.strip()

    # 2. Retrieve metadata for the requested dataset.
    try:
        dataset = get_dataset_details(dataset_id)
    except DatasetStorageError as exc:
        raise TextToSQLError(
            f"Could not load dataset: {exc}"
        ) from exc

    if not dataset:
        raise TextToSQLError("Dataset not found.")

    table_name = dataset.get("table_name")

    if not table_name:
        raise TextToSQLError(
            "Dataset table name is unavailable."
        )

    # 3. Validate the generated table name before using it in SQL.
    if not re.fullmatch(r"dataset_[a-fA-F0-9]{32}", table_name):
        raise TextToSQLError(
            "Dataset table name has an invalid format."
        )

    # 4. Read the Gemini configuration.
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    if not api_key:
        raise TextToSQLError(
            "GEMINI_API_KEY is not configured."
        )

    # 5. Inspect the requested table's schema.
    # IMPORTANT: Use this module's DEFAULT_DATABASE so tests can
    # replace it with a temporary database using monkeypatch.
    try:
        with sqlite3.connect(DEFAULT_DATABASE) as connection:
            schema_rows = connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
    except sqlite3.Error as exc:
        raise TextToSQLError(
            f"Could not read dataset schema: {exc}"
        ) from exc

    if not schema_rows:
        raise TextToSQLError(
            f"Dataset schema was not found for table: {table_name}"
        )

    schema = "\n".join(
        f"- {row[1]} ({row[2]})"
        for row in schema_rows
    )

    # 6. Ask Gemini to generate a single SQLite query.
    prompt = f"""
You generate SQLite SQL for spreadsheet analysis.

Return only one SQL query. Do not include markdown or explanations.
Only SELECT or WITH queries are allowed.
Use only the table "{table_name}" and its listed columns.
Never access other tables or datasets.
Do not use PRAGMA, ATTACH, or multiple statements.
For queries returning individual rows, use LIMIT 500.

Table: "{table_name}"

Columns:
{schema}

User question:
{question}
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        sql = (response.text or "").strip()
    except Exception as exc:
        raise TextToSQLError(
            f"Gemini SQL generation failed: {exc}"
        ) from exc

    # 7. Remove optional markdown code fences from Gemini's response.
    sql = re.sub(
        r"\A```(?:sql)?\s*|\s*```\Z",
        "",
        sql,
        flags=re.IGNORECASE,
    ).strip()

    if not sql:
        raise TextToSQLError(
            "Gemini returned an empty SQL query."
        )

    # 8. Execute SQL in read-only mode, restricted to this dataset.
    # Use the SAME database path that was used for schema inspection.
    try:
        result = execute_readonly_sql(
            sql,
            database_path=DEFAULT_DATABASE,
            allowed_table=table_name,
        )
    except SQLServiceError as exc:
        raise TextToSQLError(
            f"Generated SQL was rejected: {exc}"
        ) from exc

    # 9. Return a consistent API-friendly response.
    return {
        "status": "success",
        "dataset_id": dataset_id,
        "question": question,
        "sql": sql,
        "result": result,
    }
