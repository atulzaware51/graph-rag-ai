"""HTTP responses shared by endpoints that require Neo4j."""

from typing import Any

from fastapi.responses import JSONResponse

def neo4j_unavailable_response(
    error: Exception,
    *,
    status_code: int = 503,
) -> JSONResponse:
    """Format a credential-safe Neo4j error and preserve retry guidance for clients."""
    code = getattr(error, "code", "NEO4J_UNAVAILABLE")
    retry_after_seconds = getattr(error, "retry_after_seconds", None)
    headers = {}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    details: dict[str, Any] = {
        "code": code,
        "message": str(error),
    }
    if retry_after_seconds is not None:
        details["retry_after_seconds"] = retry_after_seconds
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": str(error),
            "error": details,
        },
        headers=headers,
    )
