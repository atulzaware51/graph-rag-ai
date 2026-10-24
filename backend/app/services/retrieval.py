
"""Keyword-based retrieval for GraphSheet AI spreadsheet chunks."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend.app.services.dataset_store import (
    DatasetStorageError,
    get_dataset_chunks,
)


class RetrievalError(ValueError):
    """Raised when a retrieval request is invalid."""


# Common words that usually do not help identify relevant spreadsheet chunks.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for",
    "from", "how", "i", "in", "is", "it", "me", "of", "on",
    "or", "please", "show", "tell", "that", "the", "this",
    "to", "was", "what", "when", "where", "which", "who",
    "with", "would", "you", "your",
}


def tokenize(text: str) -> list[str]:
    """Convert text into lowercase searchable tokens."""

    if not isinstance(text, str):
        raise RetrievalError("Text must be a string.")

    return [
        token
        for token in re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())
        if token not in STOP_WORDS
    ]


def retrieve_chunks(
    query: str,
    dataset_id: str,
    top_k: int = 5,
    database_path: str | Path = "data/graphsheet.db",
) -> list[dict[str, Any]]:
    """Retrieve and rank chunks using keyword overlap.

    Results are sorted by descending relevance score. Ties preserve the
    original chunk order. Chunks without matching query terms are omitted.
    """

    if not isinstance(query, str) or not query.strip():
        raise RetrievalError("Query must be a non-empty string.")

    if not isinstance(dataset_id, str) or not dataset_id.strip():
        raise RetrievalError("Dataset ID must be a non-empty string.")

    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise RetrievalError("top_k must be a positive integer.")

    query_tokens = tokenize(query)

    if not query_tokens:
        return []

    try:
        chunks = get_dataset_chunks(
            dataset_id=dataset_id,
            database_path=database_path,
        )
    except DatasetStorageError:
        raise
    except Exception as exc:
        raise RetrievalError(f"Could not retrieve chunks: {exc}") from exc

    query_terms = set(query_tokens)
    ranked_results: list[dict[str, Any]] = []

    for original_position, chunk in enumerate(chunks):
        chunk_text = chunk.get("text", "")

        if not isinstance(chunk_text, str) or not chunk_text.strip():
            continue

        chunk_tokens = tokenize(chunk_text)
        token_counts = Counter(chunk_tokens)

        # Score each query term by its frequency in this chunk.
        matched_terms = sorted(query_terms.intersection(token_counts))

        if not matched_terms:
            continue

        score = sum(token_counts[term] for term in matched_terms)

        ranked_results.append(
            {
                "chunk_id": chunk["chunk_id"],
                "dataset_id": chunk.get("dataset_id", dataset_id),
                "chunk_index": chunk.get("chunk_index", original_position),
                "text": chunk_text,
                "metadata": chunk.get("metadata", {}),
                "score": score,
                "matched_terms": matched_terms,
                "_original_position": original_position,
            }
        )

    ranked_results.sort(
        key=lambda result: (
            -result["score"],
            result["_original_position"],
        )
    )

    # Remove the internal sorting field from the public results.
    results = ranked_results[:top_k]

    for result in results:
        result.pop("_original_position", None)

    return results

