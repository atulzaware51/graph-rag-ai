
"""Build a validated, backend-independent knowledge graph representation."""

import hashlib
import json
from typing import Any


class GraphBuilderError(Exception):
    """Raised when graph construction encounters invalid input."""


def _require_non_empty_string(
    value: Any,
    field_name: str,
) -> str:
    """Validate and return a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise GraphBuilderError(
            f"{field_name} must be a non-empty string."
        )
    return value.strip()


def _stable_identifier(prefix: str, *parts: Any) -> str:
    """Return a deterministic, collision-resistant identifier."""
    payload = "|".join(
        ""
        if part is None
        else json.dumps(
            part,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if isinstance(part, (dict, list, tuple, set))
        else str(part)
        for part in parts
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _make_column_id(dataset_id: str, sql_name: str) -> str:
    """Return the canonical ID for a dataset column."""
    return f"column:{dataset_id}:{sql_name}"


def _make_sheet_id(dataset_id: str, sheet_name: str) -> str:
    """Create a stable sheet identifier."""
    return _stable_identifier("sheet", dataset_id, sheet_name)


def _make_record_id(dataset_id: str, sheet_name: str, row_number: int) -> str:
    """Create a stable row identifier."""
    return _stable_identifier("record", dataset_id, sheet_name, row_number)


def _make_value_id(
    dataset_id: str,
    value: Any,
    *,
    column_name: str | None = None,
    entity_type: str | None = None,
) -> str:
    """Create a stable value/entity identifier."""
    return _stable_identifier(
        "value",
        dataset_id,
        column_name or "",
        entity_type or "",
        value,
    )


def build_knowledge_graph(
    datasets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build dataset, column, evidence, and inferred relationship graph data.

    Dataset fields:
        dataset_id, filename, table_name, columns

    Column fields:
        name, sql_name, data_type

    Relationship fields:
        left, right, status, confidence

    Each relationship endpoint contains:
        dataset_id, column

    Evidence fields:
        dataset_id, chunk_id, chunk_index, text, metadata

    This function builds graph data only. Persistent writes belong in
    graph_store.py so graph construction can be tested independently.
    """
    if not isinstance(datasets, list):
        raise GraphBuilderError("Datasets must be a list.")

    if relationships is None:
        relationships = []
    if not isinstance(relationships, list):
        raise GraphBuilderError("Relationships must be a list.")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise GraphBuilderError("Evidence must be a list.")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    dataset_ids: set[str] = set()
    column_ids: set[str] = set()
    column_node_by_id: dict[str, dict[str, Any]] = {}

    # Phase 1: dataset and column nodes.
    for dataset in datasets:
        if not isinstance(dataset, dict):
            raise GraphBuilderError(
                "Each dataset must be a dictionary."
            )

        dataset_id = _require_non_empty_string(
            dataset.get("dataset_id"), "dataset_id"
        )
        filename = _require_non_empty_string(
            dataset.get("filename"), "filename"
        )
        table_name = _require_non_empty_string(
            dataset.get("table_name"), "table_name"
        )

        if dataset_id in dataset_ids:
            raise GraphBuilderError(
                f"Duplicate dataset ID: {dataset_id}"
            )
        dataset_ids.add(dataset_id)

        dataset_node_id = f"dataset:{dataset_id}"

        nodes.append(
            {
                "id": dataset_node_id,
                "type": "Dataset",
                "label": filename,
                "dataset_id": dataset_id,
                "filename": filename,
                "table_name": table_name,
                **{
                    field: dataset[field]
                    for field in ("created_at", "row_count", "column_count")
                    if field in dataset
                },
            }
        )

        columns = dataset.get("columns", [])
        if not isinstance(columns, list):
            raise GraphBuilderError(
                f"Columns for dataset {dataset_id} must be a list."
            )

        seen_sql_names: set[str] = set()

        for column in columns:
            if not isinstance(column, dict):
                raise GraphBuilderError(
                    f"Invalid column metadata in dataset {dataset_id}."
                )

            name = _require_non_empty_string(
                column.get("name"),
                f"Column name in dataset {dataset_id}",
            )
            sql_name = _require_non_empty_string(
                column.get("sql_name", name),
                f"SQL column name in dataset {dataset_id}",
            )

            if sql_name in seen_sql_names:
                raise GraphBuilderError(
                    f"Duplicate column name {sql_name!r} "
                    f"in dataset {dataset_id}."
                )
            seen_sql_names.add(sql_name)

            column_id = _make_column_id(dataset_id, sql_name)

            column_node = {
                "id": column_id,
                "type": "Column",
                "label": name,
                "name": name,
                "dataset_id": dataset_id,
                "data_type": column.get("data_type", "UNKNOWN"),
                "sql_name": sql_name,
                **{
                    field: column[field]
                    for field in (
                        "null_count",
                        "unique_count",
                        "sample_values",
                        "is_potential_key",
                    )
                    if field in column
                },
            }

            nodes.append(column_node)
            column_ids.add(column_id)
            column_node_by_id[column_id] = column_node

            edges.append(
                {
                    "id": f"contains:{dataset_id}:{sql_name}",
                    "source": dataset_node_id,
                    "target": column_id,
                    "type": "HAS_COLUMN",
                }
            )

        sheet_entries: list[dict[str, Any]] = []
        if isinstance(dataset.get("sheets"), list):
            sheet_entries = dataset["sheets"]
        elif dataset.get("sheet_name") or dataset.get("sheet"):
            sheet_entries = [
                {
                    "name": dataset.get("sheet_name")
                    or dataset.get("sheet"),
                    "row_count": dataset.get("row_count"),
                }
            ]

        seen_sheet_names: set[str] = set()
        for sheet in sheet_entries:
            if isinstance(sheet, str):
                sheet_name = _require_non_empty_string(sheet, f"Sheet name in dataset {dataset_id}")
            elif isinstance(sheet, dict):
                sheet_name = _require_non_empty_string(
                    sheet.get("name") or sheet.get("sheet_name"),
                    f"Sheet name in dataset {dataset_id}",
                )
            else:
                raise GraphBuilderError(
                    f"Invalid sheet metadata for dataset {dataset_id}."
                )
            normalized_name = sheet_name.lower()
            if normalized_name in seen_sheet_names:
                continue
            seen_sheet_names.add(normalized_name)

            sheet_id = _make_sheet_id(dataset_id, sheet_name)
            sheet_node = {
                "id": sheet_id,
                "type": "Sheet",
                "label": sheet_name,
                "dataset_id": dataset_id,
                "sheet_name": sheet_name,
                **{
                    field: sheet[field]
                    for field in ("row_count", "column_count")
                    if isinstance(sheet, dict) and field in sheet
                },
            }
            nodes.append(sheet_node)
            edges.append(
                {
                    "id": f"dataset-sheet:{dataset_id}:{sheet_name}",
                    "source": dataset_node_id,
                    "target": sheet_id,
                    "type": "HAS_SHEET",
                }
            )

        record_entries = dataset.get("records")
        if record_entries is None:
            record_entries = dataset.get("rows")
        if record_entries is None:
            record_entries = dataset.get("sample_rows")
        if record_entries is not None:
            if not isinstance(record_entries, list):
                raise GraphBuilderError(
                    f"Record metadata for dataset {dataset_id} must be a list."
                )
            record_limit = dataset.get("max_record_nodes", 128)
            if not isinstance(record_limit, int) or record_limit < 1:
                record_limit = 128
            for index, record in enumerate(record_entries[:record_limit]):
                if not isinstance(record, dict):
                    raise GraphBuilderError(
                        f"Record metadata for dataset {dataset_id} must be dictionaries."
                    )
                row_number = record.get("row_number")
                if row_number is None:
                    row_number = record.get("row_index")
                if row_number is None:
                    row_number = index + 1
                if not isinstance(row_number, int) or isinstance(row_number, bool):
                    raise GraphBuilderError(
                        f"Row numbers for dataset {dataset_id} must be integers."
                    )
                sheet_name = str(
                    record.get("sheet_name")
                    or record.get("sheet")
                    or dataset.get("sheet_name")
                    or dataset.get("table_name")
                    or "Sheet 1"
                )
                sheet_id = _make_sheet_id(dataset_id, sheet_name)
                record_id = _make_record_id(dataset_id, sheet_name, row_number)
                row_node = {
                    "id": record_id,
                    "type": "Record",
                    "label": f"Row {row_number}",
                    "dataset_id": dataset_id,
                    "sheet_name": sheet_name,
                    "row_number": row_number,
                    "values": {
                        key: value
                        for key, value in record.items()
                        if key not in {"row_number", "row_index", "sheet_name", "sheet"}
                    },
                }
                if not any(node["id"] == record_id for node in nodes):
                    nodes.append(row_node)
                if any(node["id"] == sheet_id for node in nodes):
                    edges.append(
                        {
                            "id": f"sheet-row:{dataset_id}:{sheet_name}:{row_number}",
                            "source": sheet_id,
                            "target": record_id,
                            "type": "HAS_ROW",
                        }
                    )
                else:
                    edges.append(
                        {
                            "id": f"dataset-row:{dataset_id}:{row_number}",
                            "source": dataset_node_id,
                            "target": record_id,
                            "type": "HAS_ROW",
                        }
                    )

        value_entries = dataset.get("entities")
        if value_entries is None:
            value_entries = dataset.get("entity_values")
        if value_entries is None:
            value_entries = dataset.get("value_nodes")
        if value_entries is not None:
            if not isinstance(value_entries, list):
                raise GraphBuilderError(
                    f"Value metadata for dataset {dataset_id} must be a list."
                )
            for item in value_entries[:128]:
                if not isinstance(item, dict):
                    raise GraphBuilderError(
                        f"Value metadata for dataset {dataset_id} must be dictionaries."
                    )
                value = item.get("value")
                if value is None:
                    value = item.get("name")
                if value is None:
                    continue
                column_name = item.get("column") or item.get("column_name")
                entity_type = item.get("type") or item.get("entity_type") or "Value"
                value_id = _make_value_id(
                    dataset_id,
                    value,
                    column_name=column_name,
                    entity_type=str(entity_type),
                )
                value_node = {
                    "id": value_id,
                    "type": str(entity_type).title().replace("_", " ").replace(" ", ""),
                    "label": str(value),
                    "dataset_id": dataset_id,
                    "value": value,
                    **({"column_name": column_name} if column_name else {}),
                }
                if not any(node["id"] == value_id for node in nodes):
                    nodes.append(value_node)
                source_id = column_name and _make_column_id(dataset_id, str(column_name)) or dataset_node_id
                if source_id in {node["id"] for node in nodes}:
                    edges.append(
                        {
                            "id": f"value-source:{dataset_id}:{value_id}",
                            "source": source_id,
                            "target": value_id,
                            "type": "HAS_VALUE",
                        }
                    )

    # Phase 2: spreadsheet evidence nodes and their dataset provenance.
    for item in evidence:
        if not isinstance(item, dict):
            raise GraphBuilderError(
                "Each evidence item must be a dictionary."
            )
        dataset_id = _require_non_empty_string(
            item.get("dataset_id"),
            "Evidence dataset_id",
        )
        chunk_id = _require_non_empty_string(
            item.get("chunk_id"),
            "Evidence chunk_id",
        )
        chunk_index = item.get("chunk_index")
        text = _require_non_empty_string(item.get("text"), "Evidence text")
        metadata = item.get("metadata", {})
        if dataset_id not in dataset_ids:
            raise GraphBuilderError(
                f"Evidence references unknown dataset {dataset_id!r}."
            )
        if (
            not isinstance(chunk_index, int)
            or isinstance(chunk_index, bool)
            or chunk_index < 0
        ):
            raise GraphBuilderError(
                "Evidence chunk_index must be a non-negative integer."
            )
        if not isinstance(metadata, dict):
            raise GraphBuilderError("Evidence metadata must be a dictionary.")

        evidence_id = f"evidence:{dataset_id}:{chunk_id}"
        nodes.append(
            {
                "id": evidence_id,
                "type": "Evidence",
                "label": f"Evidence chunk {chunk_index + 1}",
                "dataset_id": dataset_id,
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "text": text,
                "metadata": metadata,
            }
        )
        edges.append(
            {
                "id": f"dataset-evidence:{dataset_id}:{chunk_id}",
                "source": f"dataset:{dataset_id}",
                "target": evidence_id,
                "type": "HAS_EVIDENCE",
            }
        )

    # Phase 3: cross-dataset relationship edges.
    seen_relationships: set[tuple[str, str]] = set()

    for relationship in relationships:
        if not isinstance(relationship, dict):
            raise GraphBuilderError(
                "Each relationship must be a dictionary."
            )

        left = relationship.get("left")
        right = relationship.get("right")

        if not isinstance(left, dict) or not isinstance(right, dict):
            raise GraphBuilderError(
                "Each relationship needs left and right endpoint objects."
            )

        left_dataset_id = _require_non_empty_string(
            left.get("dataset_id"), "Left endpoint dataset_id"
        )
        right_dataset_id = _require_non_empty_string(
            right.get("dataset_id"), "Right endpoint dataset_id"
        )
        left_column = _require_non_empty_string(
            left.get("column"), "Left endpoint column"
        )
        right_column = _require_non_empty_string(
            right.get("column"), "Right endpoint column"
        )

        source = _make_column_id(left_dataset_id, left_column)
        target = _make_column_id(right_dataset_id, right_column)

        # Ignore references to columns that are not in this graph.
        if source not in column_ids or target not in column_ids:
            continue

        # Relationships must connect different columns and datasets.
        if source == target or left_dataset_id == right_dataset_id:
            continue

        pair = tuple(sorted((source, target)))
        if pair in seen_relationships:
            continue

        confidence = relationship.get("confidence", 0.0)
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0.0 <= confidence <= 1.0
        ):
            raise GraphBuilderError(
                "Relationship confidence must be between 0 and 1."
            )

        status = relationship.get("status", "candidate")
        if status not in ("candidate", "approved"):
            raise GraphBuilderError(
                "Relationship status must be 'candidate' or 'approved'."
            )

        # Optional evidence fields are validated to avoid malformed graph data.
        shared_value_count = relationship.get("shared_value_count", 0)
        if (
            not isinstance(shared_value_count, int)
            or isinstance(shared_value_count, bool)
            or shared_value_count < 0
        ):
            raise GraphBuilderError(
                "shared_value_count must be a non-negative integer."
            )

        evidence = relationship.get(
            "evidence", "No relationship evidence provided."
        )
        if not isinstance(evidence, str):
            raise GraphBuilderError(
                "Relationship evidence must be a string."
            )

        edge_id = f"relationship:{pair[0]}:{pair[1]}"

        edges.append(
            {
                "id": edge_id,
                "source": source,
                "target": target,
                "type": (
                    "RELATED_TO" if status == "approved"
                    else "CANDIDATE_MATCH"
                ),
                "status": status,
                "confidence": float(confidence),
                "shared_value_count": shared_value_count,
                "name_similarity": relationship.get(
                    "name_similarity", 0.0
                ),
                "overlap_ratio": relationship.get(
                    "overlap_ratio", 0.0
                ),
                "evidence": evidence,
                "left_column": left_column,
                "right_column": right_column,
                "left_dataset_id": left_dataset_id,
                "right_dataset_id": right_dataset_id,
            }
        )

        seen_relationships.add(pair)

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }
