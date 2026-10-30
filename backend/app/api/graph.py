"""API endpoints for the GraphSheet AI knowledge graph."""

import logging

# Import APIRouter to group related endpoints, HTTPException to handle API errors, 
# and Query to add validation and default values to URL query parameters.
from fastapi import APIRouter, HTTPException, Query

# Import custom exception class and service function for constructing the knowledge graph.
from backend.app.services.graph_service import (
    GraphServiceError,          # Custom error raised when graph construction fails
    build_graph_from_database,  # Function that fetches data and constructs the graph dictionary
)
from backend.app.services.neo4j_store import (
    Neo4jStoreError,
    get_graph,
    graph_store_status,
    is_configured,
    sync_graph,
)
# Instantiate a FastAPI router with a common URL prefix '/graph' and tag for API documentation.
router = APIRouter(prefix="/graph", tags=["Knowledge Graph"])
logger = logging.getLogger(__name__)


# Define an HTTP GET route at the root path of this router (accessible via GET /graph/).
@router.get("/")
def get_knowledge_graph(
    # Define an optional 'limit' query parameter with default value 500.
    # Validate that 'limit' is greater than or equal to 1 (ge=1) and less than or equal to 5000 (le=5000).
    limit: int = Query(default=500, ge=1, le=5000),
):
    """Return the knowledge graph built from stored datasets."""

    try:
        # Fetch and build the complete knowledge graph structure from the underlying database.
        graph = build_graph_from_database()
        if is_configured():
            try:
                sync_graph("dataset-schema", graph)
                graph = {
                    **graph,
                    **get_graph("dataset-schema"),
                    "graph_store": "neo4j",
                }
            except Neo4jStoreError as exc:
                logger.warning(
                    "Neo4j graph unavailable; serving SQLite graph (%s).",
                    exc.code,
                )
                graph["graph_store"] = "sqlite"
        else:
            graph["graph_store"] = graph_store_status()

        # Slice the 'nodes' array to contain at most 'limit' items to keep response sizes manageable.
        graph["nodes"] = graph["nodes"][:limit]

        # Keep only edges whose endpoints are present in the truncated response.
        node_ids = {node["id"] for node in graph["nodes"]}
        graph["edges"] = [
            edge
            for edge in graph["edges"][:limit]
            if edge["source"] in node_ids and edge["target"] in node_ids
        ]

        # Store the total count of node records actually being returned in this response.
        graph["returned_node_count"] = len(graph["nodes"])

        # Store the total count of edge records actually being returned in this response.
        graph["returned_edge_count"] = len(graph["edges"])

        # Return the resulting graph dictionary as a JSON response to the client.
        return graph

    # Catch specific errors thrown by the underlying graph service layer.
    except GraphServiceError as exc:
        # Convert the internal graph service error into an HTTP 500 Internal Server Error response.
        raise HTTPException(
            status_code=500,  # Return HTTP status code 500 (Server Error)
            detail="Could not build the knowledge graph.",
        ) from exc           # Maintain full exception traceback context