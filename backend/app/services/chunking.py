
"""Utilities for splitting spreadsheet data into searchable text chunks."""

from __future__ import annotations

import uuid
from typing import Any


class ChunkingError(ValueError):
    """Raised when input data or chunking configuration is invalid."""


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[str]:
    """Split text into chunks with character-based overlap."""

    if not isinstance(text, str):
        raise ChunkingError("text must be a string")

    if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
        raise ChunkingError("chunk_size must be an integer")

    if not isinstance(overlap, int) or isinstance(overlap, bool):
        raise ChunkingError("overlap must be an integer")

    if chunk_size <= 0:
        raise ChunkingError("chunk_size must be greater than zero")

    if overlap < 0:
        raise ChunkingError("overlap cannot be negative")

    if overlap >= chunk_size:
        raise ChunkingError("overlap must be smaller than chunk_size")

    text = text.strip()

    if not text:
        return []

    chunks: list[str] = []
    start = 0
    step = chunk_size - overlap

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        start += step

    return chunks


def rows_to_text_chunks(
    rows: list[dict[str, Any]],
    dataset_name: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[dict[str, Any]]:
    """Convert spreadsheet rows into text chunks with source metadata.

    Each row should be a dictionary whose keys are spreadsheet column names.
    Row numbers are one-based and refer to data rows, excluding the header.
    """

    if not isinstance(rows, list):
        raise ChunkingError("rows must be a list of dictionaries")

    if not isinstance(dataset_name, str) or not dataset_name.strip():
        raise ChunkingError("dataset_name must be a non-empty string")

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ChunkingError(
                f"row {index + 1} must be a dictionary"
            )

        if not all(isinstance(key, str) for key in row):
            raise ChunkingError(
                f"all column names in row {index + 1} must be strings"
            )

    # Validate chunk_size and overlap using the shared validator.
    chunk_text("validation", chunk_size, overlap)

    if not rows:
        return []

    row_texts: list[tuple[int, str]] = []

    for row_number, row in enumerate(rows, start=1):
        values = [
            f"{column}: {value}"
            for column, value in row.items()
            if value is not None
        ]

        if not values:
            continue

        row_text = (
            f"Dataset: {dataset_name}\n"
            f"Source row: {row_number}\n"
            + "\n".join(values)
        )

        row_texts.append((row_number, row_text))

    if not row_texts:
        return []

    chunks: list[dict[str, Any]] = []
    current_text = ""
    current_rows: list[int] = []

    def add_chunk(text: str, source_rows: list[int]) -> None:
        """Append a chunk with a unique ID and source metadata."""

        if not text.strip():
            return

        normalized_rows = sorted(set(source_rows))

        chunks.append(
            {
                # A fresh ID prevents collisions across separate uploads.
                "chunk_id": uuid.uuid4().hex,
                "text": text,
                "metadata": {
                    "dataset_name": dataset_name,
                    "source_rows": normalized_rows,
                },
            }
        )

    for row_number, row_text in row_texts:
        # Split rows that exceed the configured chunk size.
        if len(row_text) > chunk_size:
            if current_text:
                add_chunk(current_text, current_rows)
                current_text = ""
                current_rows = []

            for part in chunk_text(row_text, chunk_size, overlap):
                add_chunk(part, [row_number])

            continue

        separator = "\n\n"

        candidate = (
            f"{current_text}{separator}{row_text}"
            if current_text
            else row_text
        )

        if len(candidate) <= chunk_size:
            current_text = candidate
            current_rows.append(row_number)
        else:
            # Save the current chunk before starting the next one.
            add_chunk(current_text, current_rows)

            # Carry a character suffix forward for contextual overlap.
            prefix = current_text[-overlap:] if overlap else ""

            current_text = (
                f"{prefix}\n\n{row_text}"
                if prefix
                else row_text
            )

            # Track the source rows visible in the preserved prefix.
            current_rows = [
                int(line.split(":", 1)[1].strip())
                for line in prefix.splitlines()
                if line.startswith("Source row:")
            ]

            current_rows.append(row_number)

            # If the overlap makes this chunk too large, save it
            # rather than exceeding the configured size.
            if len(current_text) > chunk_size:
                add_chunk(current_text, current_rows)
                current_text = ""
                current_rows = []

    if current_text:
        add_chunk(current_text, current_rows)

    return chunks
