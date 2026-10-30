
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

    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    normalized_tokens: list[str] = []
    for token in tokens:
        if token in STOP_WORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith(("sses", "shes", "ches", "xes", "zes")):
            token = token[:-2]
        elif (
            token.endswith("s")
            and len(token) > 3
            and not token.endswith(("ss", "us", "is"))
            and token not in {"series", "species"}
        ):
            token = token[:-1]
        normalized_tokens.append(token)
    return normalized_tokens


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


def retrieve_chunks_for_datasets(
    query: str,
    dataset_ids: list[str],
    top_k: int = 5,
    database_path: str | Path = "data/graphsheet.db",
    graph_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve selected-sheet evidence, guided by relevant graph columns."""
    if not isinstance(query, str) or not query.strip():
        raise RetrievalError("Query must be a non-empty string.")
    if not isinstance(dataset_ids, list) or not dataset_ids:
        raise RetrievalError("Select at least one dataset.")
    if len(dataset_ids) > 5:
        raise RetrievalError("A chat can use at most five datasets.")
    if any(not isinstance(value, str) or not value.strip() for value in dataset_ids):
        raise RetrievalError("Dataset IDs must be non-empty strings.")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        raise RetrievalError("top_k must be a positive integer.")

    unique_ids = list(dict.fromkeys(dataset_ids))
    query_tokens = set(tokenize(query))
    retrieval_context = graph_context or {}
    anchors_by_dataset: dict[str, list[dict[str, Any]]] = {}
    graph_chunks_by_dataset: dict[str, list[dict[str, Any]]] = {}
    if isinstance(retrieval_context, dict):
        graph_nodes = retrieval_context.get("nodes", [])
        if isinstance(graph_nodes, list):
            for node in graph_nodes:
                if (
                    not isinstance(node, dict)
                    or node.get("type") != "Evidence"
                    or node.get("dataset_id") not in unique_ids
                ):
                    continue
                chunk_id = node.get("chunk_id")
                text = node.get("text")
                chunk_index = node.get("chunk_index")
                if (
                    not isinstance(chunk_id, str)
                    or not chunk_id
                    or not isinstance(text, str)
                    or not text.strip()
                    or not isinstance(chunk_index, int)
                    or isinstance(chunk_index, bool)
                ):
                    continue
                metadata = node.get("metadata", {})
                graph_chunks_by_dataset.setdefault(
                    node["dataset_id"],
                    [],
                ).append(
                    {
                        "chunk_id": chunk_id,
                        "dataset_id": node["dataset_id"],
                        "chunk_index": chunk_index,
                        "text": text,
                        "metadata": (
                            metadata if isinstance(metadata, dict) else {}
                        ),
                    }
                )

        retrieval = retrieval_context.get("retrieval", {})
        if isinstance(retrieval, dict):
            for column in retrieval.get("relevant_columns", []):
                if not isinstance(column, dict):
                    continue
                column_dataset_id = column.get("dataset_id")
                if column_dataset_id not in unique_ids:
                    continue
                anchors_by_dataset.setdefault(column_dataset_id, []).append(
                    column
                )

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for position, dataset_id in enumerate(unique_ids):
        chunks = graph_chunks_by_dataset.get(dataset_id)
        if chunks is None:
            try:
                chunks = get_dataset_chunks(
                    dataset_id=dataset_id,
                    database_path=database_path,
                )
            except DatasetStorageError:
                raise
            except Exception as exc:
                raise RetrievalError(
                    f"Could not retrieve chunks for dataset {dataset_id!r}."
                ) from exc

        graph_columns = anchors_by_dataset.get(dataset_id, [])
        for original_position, chunk in enumerate(chunks):
            chunk_text = chunk.get("text", "")
            if not isinstance(chunk_text, str) or not chunk_text.strip():
                continue

            token_counts = Counter(tokenize(chunk_text))
            matched_terms = sorted(query_tokens.intersection(token_counts))
            graph_matches = []
            graph_score = 0
            for column in graph_columns:
                column_terms = set(
                    tokenize(
                        " ".join(
                            value
                            for value in (
                                column.get("label"),
                                column.get("sql_name"),
                            )
                            if isinstance(value, str)
                        )
                    )
                )
                matched_column_terms = sorted(
                    column_terms.intersection(token_counts)
                )
                if matched_column_terms:
                    graph_score += sum(
                        token_counts[term] for term in matched_column_terms
                    )
                    graph_matches.append(
                        {
                            "column": column.get("label")
                            or column.get("sql_name"),
                            "sql_name": column.get("sql_name"),
                            "matched_terms": matched_column_terms,
                        }
                    )

            if not matched_terms and not graph_matches:
                continue

            ranked_result = {
                "chunk_id": chunk["chunk_id"],
                "dataset_id": chunk.get("dataset_id", dataset_id),
                "chunk_index": chunk.get("chunk_index", original_position),
                "text": chunk_text,
                "metadata": chunk.get("metadata", {}),
                "score": sum(token_counts[term] for term in matched_terms)
                + graph_score,
                "matched_terms": matched_terms,
                "graph_matches": graph_matches,
                "_dataset_position": position,
                "_original_position": original_position,
            }
            ranked.append(
                (
                    position,
                    original_position,
                    ranked_result,
                )
            )

    ranked.sort(
        key=lambda item: (
            -item[2]["score"],
            item[0],
            item[2]["chunk_index"],
            item[1],
        )
    )
    results = [result for _, _, result in ranked[:top_k]]
    for result in results:
        result.pop("_dataset_position", None)
        result.pop("_original_position", None)
    return results


def build_graph_retrieval_context(
    query: str,
    dataset_ids: list[str],
    graph_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Select query-relevant columns and their candidate graph neighbors."""
    context = dict(graph_context or {})
    if not isinstance(query, str) or not query.strip():
        raise RetrievalError("Query must be a non-empty string.")
    if not isinstance(dataset_ids, list) or not dataset_ids:
        raise RetrievalError("Select at least one dataset.")

    selected_ids = set(dataset_ids)
    query_tokens = set(tokenize(query))
    nodes = context.get("nodes", [])
    if not isinstance(nodes, list):
        nodes = []

    columns_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    matched_keys: set[tuple[str, str]] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "Column":
            continue
        dataset_id = node.get("dataset_id")
        if dataset_id not in selected_ids:
            continue
        sql_name = node.get("sql_name")
        label = node.get("label")
        column_name = sql_name if isinstance(sql_name, str) else label
        if not isinstance(column_name, str) or not column_name:
            continue
        key = (dataset_id, column_name.casefold())
        columns_by_key[key] = node
        column_terms = set(
            tokenize(
                " ".join(
                    value
                    for value in (label, sql_name)
                    if isinstance(value, str)
                )
            )
        )
        if query_tokens.intersection(column_terms):
            matched_keys.add(key)

    candidate_relationships = context.get("candidate_relationships", [])
    if not isinstance(candidate_relationships, list):
        candidate_relationships = []
    relevant_keys = set(matched_keys)
    matched_relationships: list[dict[str, Any]] = []
    for relationship in candidate_relationships:
        if not isinstance(relationship, dict):
            continue
        endpoints = [
            relationship.get("left"),
            relationship.get("right"),
        ]
        endpoint_keys: list[tuple[str, str] | None] = []
        endpoints_valid = True
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                endpoint_keys.append(None)
                endpoints_valid = False
                continue
            endpoint_dataset = endpoint.get("dataset_id")
            endpoint_column = endpoint.get("column")
            if (
                endpoint_dataset not in selected_ids
                or not isinstance(endpoint_column, str)
            ):
                endpoint_keys.append(None)
                endpoints_valid = False
                continue
            key = (endpoint_dataset, endpoint_column.casefold())
            endpoint_keys.append(key)
        if not endpoints_valid or not any(
            key in matched_keys for key in endpoint_keys if key
        ):
            continue
        matched_relationships.append(relationship)
        relevant_keys.update(
            key
            for key in endpoint_keys
            if key is not None and key in columns_by_key
        )

    relevant_columns = [
        columns_by_key[key]
        for key in columns_by_key
        if key in relevant_keys
    ]

    def column_summary(column: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in column.items()
            if key != "sample_values"
        }

    context["retrieval"] = {
        "matched_columns": [
            column_summary(columns_by_key[key])
            for key in columns_by_key
            if key in matched_keys
        ],
        "related_columns": [
            column_summary(columns_by_key[key])
            for key in columns_by_key
            if key in relevant_keys and key not in matched_keys
        ],
        "relevant_columns": [
            column_summary(column) for column in relevant_columns
        ],
        "matched_relationships": matched_relationships,
    }
    return context
