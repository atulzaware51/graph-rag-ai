"""Build a graph representation from stored dataset metadata."""
# This module-level docstring explains the purpose of the script: converting dataset/relationship data into a graph structure (nodes and edges).

# Import Any from the typing module to allow flexible type hinting for dictionaries
from typing import Any


class GraphBuilderError(Exception):
    """Raised when graph construction encounters invalid input."""
    # Define a custom exception specific to errors that occur during the graph building process


def build_knowledge_graph(
    datasets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build graph nodes and edges from datasets and candidates.

    Expected dataset fields:
        dataset_id, filename, table_name, columns

    Expected column fields:
        name, sql_name, data_type

    Expected relationship fields:
        left, right, status, confidence

    Relationship endpoints should identify a dataset and column.
    """

    # Initialize empty lists to store the final graph components
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    
    # Initialize sets to keep track of unique IDs. 
    # Sets provide fast O(1) lookups to check for duplicates later.
    dataset_ids: set[str] = set()
    column_ids: set[str] = set()

    # Define a nested helper function to generate a consistent, unique ID for every column
    def make_column_id(dataset_id: str, sql_name: str) -> str:
        return f"column:{dataset_id}:{sql_name}"

    # --- PHASE 1: Create dataset and column nodes ---
    # Iterate through every dataset provided in the input list
    for dataset in datasets:
        # Extract the core string attributes for the dataset
        dataset_id = dataset.get("dataset_id")
        filename = dataset.get("filename")
        table_name = dataset.get("table_name")

        # Validate that all three core attributes exist, are strings, and are not empty
        if not all(
            isinstance(value, str) and value.strip()
            for value in (dataset_id, filename, table_name)
        ):
            # Raise an error if any of the required fields are missing or invalid
            raise GraphBuilderError(
                "Each dataset needs non-empty dataset_id, filename, "
                "and table_name strings."
            )

        # Check if we have already processed a dataset with this ID
        if dataset_id in dataset_ids:
            raise GraphBuilderError(
                f"Duplicate dataset ID: {dataset_id}"
            )

        # Mark this dataset ID as seen
        dataset_ids.add(dataset_id)

        # Format a globally unique node ID for the dataset
        dataset_node_id = f"dataset:{dataset_id}"

        # Append the dataset representation as a "Node" to our graph
        nodes.append(
            {
                "id": dataset_node_id,
                "type": "Dataset",
                "label": filename,
                "dataset_id": dataset_id,
                "table_name": table_name,
            }
        )

        # Extract the list of columns for this dataset (defaulting to an empty list if missing)
        columns = dataset.get("columns", [])
        # Ensure the columns data is actually a list structure
        if not isinstance(columns, list):
            raise GraphBuilderError(
                f"Columns for dataset {dataset_id} must be a list."
            )

        # Physical SQL names identify columns in inference and must be unique.
        seen_sql_names: set[str] = set()

        # Iterate through every column in the current dataset
        for column in columns:
            # Ensure the column representation is a dictionary
            if not isinstance(column, dict):
                raise GraphBuilderError(
                    f"Invalid column metadata in dataset {dataset_id}."
                )

            # Extract the column name
            name = column.get("name")
            # Ensure the column name is a valid, non-empty string
            if not isinstance(name, str) or not name.strip():
                raise GraphBuilderError(
                    f"Invalid column name in dataset {dataset_id}."
                )

            sql_name = column.get("sql_name", name)
            if not isinstance(sql_name, str) or not sql_name.strip():
                raise GraphBuilderError(
                    f"Invalid SQL column name in dataset {dataset_id}."
                )

            # Check if this physical column was already defined in this dataset.
            if sql_name in seen_sql_names:
                raise GraphBuilderError(
                    f"Duplicate column name {sql_name!r} "
                    f"in dataset {dataset_id}."
                )

            seen_sql_names.add(sql_name)
            
            # Generate a globally unique ID for this column using our helper function
            column_id = make_column_id(dataset_id, sql_name)

            # Construct the graph node dictionary for this column
            column_node = {
                "id": column_id,
                "type": "Column",
                "label": name,
                "dataset_id": dataset_id,
                "data_type": column.get("data_type", "UNKNOWN"), # Fallback to "UNKNOWN" if missing
                "sql_name": sql_name,
            }

            # Add the column node to our master graph nodes list
            nodes.append(column_node)
            # Register the globally unique column ID in our tracking set
            column_ids.add(column_id)

            # Create an "Edge" (relationship line) linking the parent dataset to this child column
            edges.append(
                {
                    "id": f"contains:{dataset_id}:{sql_name}",
                    "source": dataset_node_id,
                    "target": column_id,
                    "type": "HAS_COLUMN", # Describes a structural hierarchy
                }
            )

    # --- PHASE 2: Create candidate edges for inferred relationships ---
    # Set to track uniquely paired column relationships (to avoid bidirectional duplicates like A->B and B->A)
    seen_relationships: set[tuple[str, str]] = set()

    # Iterate through all proposed relationships provided to the function
    for relationship in relationships:
        # Ensure the relationship data is formatted as a dictionary
        if not isinstance(relationship, dict):
            raise GraphBuilderError(
                "Each relationship must be a dictionary."
            )

        # Extract the endpoints of the relationship
        left = relationship.get("left")
        right = relationship.get("right")

        # Ensure both endpoints exist and are dictionaries
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise GraphBuilderError(
                "Each relationship needs left and right endpoint objects."
            )

        # Extract the dataset and column identifiers for both the left and right endpoints
        left_dataset_id = left.get("dataset_id")
        right_dataset_id = right.get("dataset_id")
        left_column = left.get("column")
        right_column = right.get("column")

        # Validate that all extracted identifiers are valid, non-empty strings
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                left_dataset_id,
                right_dataset_id,
                left_column,
                right_column,
            )
        ):
            raise GraphBuilderError(
                "Relationship endpoints need dataset IDs and column names."
            )

        # Reconstruct the expected global graph node IDs for both endpoint columns
        source = make_column_id(left_dataset_id, left_column)
        target = make_column_id(right_dataset_id, right_column)

        # Integrity Check: Ensure both columns actually exist in the graph we built in Phase 1.
        if source not in column_ids or target not in column_ids:
            # Ignore candidates that reference unknown graph columns.
            continue

        # Prevent a column from having a relationship with itself (self-loop)
        if source == target:
            continue

        # Prevent relationships between columns inside the same dataset
        if left_dataset_id == right_dataset_id:
            continue

        # Create a sorted tuple of the two column IDs. 
        # Sorting guarantees that (A, B) and (B, A) result in the exact same tuple, preventing bidirectional duplicates.
        pair = tuple(sorted((source, target)))
        if pair in seen_relationships:
            # Skip if we already mapped a relationship between these two columns
            continue

        # Register this pair as seen
        seen_relationships.add(pair)

        # Extract the confidence score, defaulting to 0.0 if not provided
        confidence = relationship.get("confidence", 0.0)
        
        # Validate that confidence is a number (int or float).
        # We explicitly check `not isinstance(..., bool)` because in Python, True/False are technically subclasses of int (1/0).
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not 0.0 <= confidence <= 1.0 # Ensure it falls within 0-100% bounds
        ):
            raise GraphBuilderError(
                "Relationship confidence must be between 0 and 1."
            )

        # Extract the relationship status, defaulting to "candidate"
        relationship_status = relationship.get("status", "candidate")
        # Ensure it is one of the two strictly allowed values
        if relationship_status not in ("candidate", "approved"):
            raise GraphBuilderError(
                "Relationship status must be 'candidate' or 'approved'."
            )


                # Add the cross-dataset column relationship as an edge.
        edges.append(
            {
                "id": f"relationship:{len(seen_relationships)}",
                "source": source,
                "target": target,
                "type": (
                    "RELATED_TO"
                    if relationship_status == "approved"
                    else "CANDIDATE_MATCH"
                ),
                "status": relationship_status,
                "confidence": float(confidence),

                # Preserve the evidence generated by relationship inference.
                "shared_value_count": relationship.get(
                    "shared_value_count", 0
                ),
                "name_similarity": relationship.get(
                    "name_similarity", 0.0
                ),
                "overlap_ratio": relationship.get(
                    "overlap_ratio", 0.0
                ),
                "evidence": relationship.get(
                    "evidence",
                    "No relationship evidence provided.",
                ),

                # Preserve endpoint information for the frontend.
                "left_column": left_column,
                "right_column": right_column,
                "left_dataset_id": left_dataset_id,
                "right_dataset_id": right_dataset_id,
            }
        )


    # --- Return the final constructed graph ---
    return {
        "node_count": len(nodes),     # Total number of Datasets + Columns
        "edge_count": len(edges),     # Total number of HAS_COLUMN + cross-dataset relationships
        "nodes": nodes,               # List of all node dictionaries
        "edges": edges,               # List of all edge dictionaries
    }