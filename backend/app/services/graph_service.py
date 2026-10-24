
"""Integrate stored datasets and inferred relationships into a graph."""

from pathlib import Path
from typing import Any

from backend.app.services.dataset_reader import (
    DatasetReaderError,
    DatasetNotFoundError,
    get_dataset_details,
    list_datasets,
)
from backend.app.services.graph_builder import (
    GraphBuilderError,
    build_knowledge_graph,
)
from backend.app.services.relationship_inference import (
    RelationshipInferenceError,
    infer_relationships,
)
from backend.app.services.schema_profiler import (
    SchemaProfilerError,
    profile_dataset,
)


DEFAULT_DATABASE_PATH = Path("data/graphsheet.db")


class GraphServiceError(Exception):
    """Raised when the knowledge graph cannot be assembled."""


def build_graph_from_database(
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict[str, Any]:
    """Build a graph from stored dataset schemas and inferred links."""

    database = Path(database_path)

    try:
        stored_datasets = list_datasets(database)

        graph_datasets: list[dict[str, Any]] = []

        for dataset in stored_datasets:
            dataset_id = dataset["dataset_id"]
            schema = profile_dataset(
                dataset_id,
                database_path=database,
            )
            stored_dataset = get_dataset_details(
                dataset_id,
                database_path=database,
            )

            columns = schema.get("columns", [])
            stored_columns = stored_dataset.get("columns", [])
            stored_columns_by_sql_name = {
                column.get("sql_name"): column
                for column in stored_columns
                if isinstance(column.get("sql_name"), str)
            }

            if (
                len(stored_columns_by_sql_name) != len(stored_columns)
                or set(stored_columns_by_sql_name)
                != {
                    column.get("name")
                    for column in columns
                    if isinstance(column.get("name"), str)
                }
            ):
                raise GraphServiceError(
                    "Stored column metadata does not match the physical "
                    f"schema for dataset {dataset_id!r}."
                )

            graph_datasets.append(
                {
                    "dataset_id": dataset_id,
                    "filename": dataset.get(
                        "original_filename",
                        dataset.get("filename", ""),
                    ),
                    "table_name": dataset["table_name"],
                    "columns": [
                        {
                            "name": stored_columns_by_sql_name[
                                column["name"]
                            ]["original_name"],
                            "sql_name": column["name"],
                            "data_type": column.get(
                                "data_type",
                                column.get("sqlite_type", "UNKNOWN"),
                            ),
                        }
                        for column in columns
                    ],
                }
            )

        inference = infer_relationships(database)
        relationships = inference.get("relationships", [])

        graph = build_knowledge_graph(
            datasets=graph_datasets,
            relationships=relationships,
        )

        return {
            "status": "success",
            "dataset_count": len(graph_datasets),
            "relationship_candidate_count": len(relationships),
            **graph,
        }

    except (
        DatasetReaderError,
        DatasetNotFoundError,
        SchemaProfilerError,
        RelationshipInferenceError,
        GraphBuilderError,
    ) as exc:
        raise GraphServiceError(
            f"Could not build knowledge graph: {exc}"
        ) from exc
