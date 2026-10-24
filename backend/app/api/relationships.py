"""API endpoints for inferred dataset relationships."""
# This module-level docstring explains the purpose of the file: defining API routes for relationship inference.

# Import APIRouter to group endpoints, HTTPException to return HTTP errors, and status for standard HTTP status codes
from fastapi import APIRouter, HTTPException, status

# Import the relationship inference function and its specific custom error class from the backend services
from backend.app.services.relationship_inference import (
    RelationshipInferenceError,
    infer_relationships,
)

# Initialize a new FastAPI router object. All routes defined with this router will automatically 
# have the URL prefix "/relationships" and be grouped under the "Relationships" tag in the API documentation
router = APIRouter(prefix="/relationships", tags=["Relationships"])


# Define a GET HTTP endpoint for the root of this router's prefix (i.e., GET /relationships/)
@router.get("/")
def get_relationships():
    """Return candidate relationships across stored datasets."""
    try:
        # Call the backend service function to analyze and infer relationships between all datasets
        result = infer_relationships()

        # Return a dictionary combining a manual "status" key with all the key-value pairs 
        # returned by the `infer_relationships()` function (using the ** unpacking operator)
        return {
            "status": "success",
            **result,
        }

    # Catch the specific exception raised if the relationship inference process fails
    except RelationshipInferenceError as exc:
        # Wrap the backend exception in a FastAPI HTTPException so the API client receives 
        # a proper 500 Internal Server Error response, along with the error details
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not infer dataset relationships.",
        ) from exc