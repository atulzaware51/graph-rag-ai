# Import the main FastAPI class to construct the web application
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Import the documents endpoint router from the application's API module
from backend.app.api.documents import router as documents_router

from backend.app.api.datasets import router as datasets_router

from backend.app.api.graph import router as graph_router
from backend.app.api.relationships import router as relationships_router
from backend.app.api.conversations import router as conversations_router
# Initialize the core FastAPI application instance with metadata for interactive API docs
app = FastAPI(
    title="GraphSheet AI API",
    description=(
        "Knowledge Graph RAG, spreadsheet analysis, "
        "and Text-to-SQL platform"
    ),
    version="0.1.0",
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
    }


app.mount(
    "/app",
    StaticFiles(
        directory=Path(__file__).parent / "static",
        html=True,
    ),
    name="frontend",
)