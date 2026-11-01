"""Dataset listing, details, schema profiling, search, and question answering endpoints."""
#from backend.app.services.text_to_sql_service import (
  #  TextToSQLError,
 #   ask_with_sql,
#)

from backend.app.services.text_to_sql_service import TextToSQLError
from backend.app.services.text_to_sql_graph import ask_with_sql_graph

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.services.answer_service import (
    AnswerServiceError,
    answer_question,
)


from backend.app.services.dataset_reader import (
    DatasetNotFoundError,
    DatasetReaderError,
    get_dataset_details,
    get_dataset_preview,
    list_datasets,
)
from backend.app.services.dataset_store import (
    DatasetStorageError,
    delete_spreadsheet,
)
from backend.app.services.retrieval import RetrievalError, retrieve_chunks
from backend.app.services.schema_profiler import (
    SchemaProfilerError,
    profile_dataset,
)
from backend.app.services.graph_service import (
    GraphServiceError,
    build_graph_for_scope,
)
from backend.app.services.neo4j_store import Neo4jStoreError
from backend.app.api.neo4j_errors import neo4j_unavailable_response


router = APIRouter(prefix="/datasets", tags=["Datasets"])


class AskRequest(BaseModel):
    """Request body for spreadsheet question answering."""

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)

class AskSQLRequest(BaseModel):
    """Request body for natural-language SQL analysis."""

    question: str = Field(min_length=1, max_length=2000)
@router.get("/")
def get_all_datasets():
    """List all datasets saved in SQLite."""
    try:
        datasets = list_datasets()
        return {
            "status": "success",
            "count": len(datasets),
            "datasets": datasets,
        }
    except DatasetReaderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not list datasets.",
        ) from exc


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str):
    """Delete a dataset and all associated graph and conversation data."""
    try:
        deleted = delete_spreadsheet(dataset_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dataset not found.",
            )
        return {
            "status": "success",
            "dataset_id": dataset_id,
            "deleted": True,
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Neo4jStoreError as exc:
        return neo4j_unavailable_response(exc)
    except DatasetStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not delete the dataset.",
        ) from exc


@router.get("/{dataset_id}/schema")
def get_dataset_schema(dataset_id: str):
    """Return schema statistics for a stored dataset."""
    try:
        schema = profile_dataset(dataset_id)
        return {
            "status": "success",
            "schema": schema,
        }
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found.",
        ) from exc
    except (SchemaProfilerError, DatasetReaderError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not profile the dataset.",
        ) from exc


@router.get("/{dataset_id}/preview")
def get_dataset_rows(
    dataset_id: str,
    limit: int = Query(default=10, ge=1, le=100),
):
    """Return a bounded preview of stored dataset rows."""
    try:
        return {
            "status": "success",
            "dataset_id": dataset_id,
            "rows": get_dataset_preview(dataset_id, limit=limit),
        }
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found.",
        ) from exc
    except DatasetReaderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not read the dataset preview.",
        ) from exc


@router.get("/{dataset_id}/search")
def search_dataset(
    dataset_id: str,
    query: str = Query(min_length=1, max_length=2000),
    top_k: int = Query(default=5, ge=1, le=20),
):
    """Search a dataset and return its most relevant text chunks."""
    try:
        # Verify the dataset exists before searching.
        get_dataset_details(dataset_id)

        results = retrieve_chunks(
            query=query,
            dataset_id=dataset_id,
            top_k=top_k,
        )

        return {
            "status": "success",
            "dataset_id": dataset_id,
            "query": query,
            "count": len(results),
            "results": results,
        }

    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found.",
        ) from exc
    except RetrievalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DatasetReaderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve dataset details.",
        ) from exc


@router.post("/{dataset_id}/ask")
def ask_dataset_question(
    dataset_id: str,
    request: AskRequest,
):
    """Answer a question using retrieved spreadsheet evidence and Gemini."""
    try:
        # Verify the dataset exists before attempting retrieval or generation.
        get_dataset_details(dataset_id)

        result = answer_question(
            query=request.query,
            dataset_ids=[dataset_id],
            top_k=request.top_k,
            graph_context=build_graph_for_scope(
                f"dataset-query:{dataset_id}",
                dataset_ids=[dataset_id],
                include_evidence=True,
            ),
        )
        return result

    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found.",
        ) from exc
    except DatasetStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load dataset data.",
        ) from exc
    except RetrievalError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except DatasetReaderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not retrieve dataset details.",
        ) from exc
    except AnswerServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except GraphServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build graph context for the dataset.",
        ) from exc

# 




@router.post("/{dataset_id}/ask-sql")
def ask_dataset_with_sql(
    dataset_id: str,
    request: AskSQLRequest,
):
    """Generate and execute a read-only SQL query through LangGraph."""

    try:
        result = ask_with_sql_graph(
            question=request.question,
            dataset_id=dataset_id,
            graph_context=build_graph_for_scope(
                f"dataset-query:{dataset_id}",
                dataset_ids=[dataset_id],
            ),
        )

        # LangGraph returns an error state for handled workflow failures.
        if result.get("status") == "error":
            message = result.get(
                "error",
                "Text-to-SQL workflow failed.",
            )
            error_code = result.get("error_code")

            # The requested dataset does not exist.
            if message == "Dataset not found.":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )

            # Invalid input should be reported as a client error.
            if message in {
                "Question must not be empty.",
                "Dataset ID must not be empty.",
            }:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=message,
                )

            # Missing provider keys are a server configuration problem.
            if error_code == "LLM_PROVIDER_NOT_CONFIGURED":
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Text-to-SQL service is not configured.",
                )

            if error_code == "LLM_PROVIDERS_UNAVAILABLE":
                raise HTTPException(
                    status_code=(
                        status.HTTP_503_SERVICE_UNAVAILABLE
                        if result.get("retryable")
                        else status.HTTP_502_BAD_GATEWAY
                    ),
                    detail={
                        "code": error_code,
                        "message": message,
                        "retryable": bool(result.get("retryable")),
                    },
                )

            # Other handled workflow errors.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=message,
            )

        # Validate the workflow output before accessing its result.
        if not isinstance(result, dict) or not isinstance(
            result.get("result"), dict
        ):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid response from the Text-to-SQL workflow.",
            )

        return result["result"]

    except HTTPException:
        # Preserve the HTTP status and detail selected above.
        raise

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    except TextToSQLError as exc:
        message = str(exc)

        if message == "Dataset not found.":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc

        # Do not expose unexpected service internals.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Text-to-SQL request could not be completed.",
        ) from exc

    except GraphServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not build graph context for the dataset.",
        ) from exc

    except Exception as exc:
        # Keep unexpected internal details out of the API response.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Text-to-SQL server error.",
        ) from exc

# 

@router.get("/{dataset_id}")
def get_dataset(dataset_id: str):
    """Retrieve dataset metadata and column details."""
    try:
        dataset = get_dataset_details(dataset_id)
        return {
            "status": "success",
            "dataset": dataset,
        }
    except DatasetNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except DatasetReaderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc
