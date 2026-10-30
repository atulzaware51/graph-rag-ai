# Import the main FastAPI class to construct the web application
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

# Import the documents endpoint router from the application's API module
from backend.app.api.documents import router as documents_router

from backend.app.api.datasets import router as datasets_router

from backend.app.api.graph import router as graph_router
from backend.app.api.relationships import router as relationships_router
from backend.app.api.conversations import router as conversations_router
from backend.app.api.neo4j_errors import neo4j_unavailable_response
from backend.app.services.neo4j_store import (
    Neo4jStoreError,
    check_connectivity,
    close_driver,
    connection_info,
)
from backend.app.services.conversation_store import DEFAULT_DATABASE


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Release the shared Neo4j driver when the API process shuts down."""
    try:
        yield
    finally:
        close_driver()


# Initialize the core FastAPI application instance with metadata for interactive API docs
app = FastAPI(
    title="GraphSheet AI API",
    description=(
        "Knowledge Graph RAG, spreadsheet analysis, "
        "and Text-to-SQL platform"
    ),
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Attach the documents router and its associated endpoints to the main FastAPI app
app.include_router(documents_router)

# attach the dataset router and its associated endpoint to the main fast api app
app.include_router(datasets_router)


# attach the graph router and its associated endpoints to the main fast api app
app.include_router(graph_router)
# attach the dataset router and its associated endpoints to the main fastapi app :
app.include_router(relationships_router)
app.include_router(conversations_router)
# Define a GET route at the root URL path ("/")
@app.get("/")
# Define the root endpoint handler returning basic application status and version info
def root():
    return {
        "message": "GraphSheet AI API is running",
        "status": "success",
        "version": "0.1.0",
    }


# Define a GET route at the "/health" path for health check monitoring
@app.get("/health")
# Define the health check handler used by orchestrators (e.g., Docker, Kubernetes) to verify service availability
def health_check():
    return {
        "status": "healthy",
        "service": "GraphSheet AI API",
        "checks": {
            "sqlite": sqlite_readiness(),
            "neo4j": {
                "configured": is_neo4j_configured(),
                "status": "not_checked",
                "readiness": "/ready/neo4j",
            }
        },
    }


def sqlite_readiness() -> dict[str, bool | str | None]:
    """Report SQLite availability without creating an uninitialized database."""
    database = Path(DEFAULT_DATABASE)
    if not database.is_file():
        return {"available": None, "status": "not_initialized"}

    try:
        database_uri = f"{database.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True, timeout=1) as connection:
            connection.execute("SELECT 1").fetchone()
    except (OSError, sqlite3.Error):
        return {"available": False, "status": "unavailable"}
    return {"available": True, "status": "available"}


def is_neo4j_configured() -> bool:
    """Read the connection configuration without testing the network."""
    try:
        return bool(connection_info()["configured"])
    except Neo4jStoreError:
        return True


@app.get("/ready/neo4j")
def neo4j_readiness():
    """Check Neo4j connectivity independently from API liveness."""
    try:
        result = check_connectivity()
        if not result["ready"]:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "neo4j": result,
                    "error": {
                        "code": "NEO4J_NOT_CONFIGURED",
                        "message": "Neo4j is not configured.",
                    },
                },
            )
        return {
            "status": "ready",
            "neo4j": result,
        }
    except Neo4jStoreError as exc:
        return neo4j_unavailable_response(exc)


app.mount(
    "/app",
    StaticFiles(
        directory=Path(__file__).parent / "static",
        html=True,
    ),
    name="frontend",
)