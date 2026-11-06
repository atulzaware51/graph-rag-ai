"""Handle spreadsheet uploads and return prepared dataset information."""

# Import Path class for object-oriented filesystem path manipulation
from pathlib import Path
# Import NamedTemporaryFile utility to create disk-backed temporary files
from tempfile import NamedTemporaryFile

# Import FastAPI routing, file upload, status codes, and HTTP exception handling utilities
from fastapi import APIRouter, File, HTTPException, UploadFile, status
import logging

# Import dataset storage error class and store function from database storage service
from backend.app.services.dataset_store import (
    DatasetStorageError,  # Custom exception raised when storing data or chunks fails
    delete_spreadsheet,
    store_spreadsheet,    # Function to save dataset rows, metadata, and chunks into SQLite
    update_dataset_graph_status,
)
# Import spreadsheet ingestion error class and inspector function from ingestion service
from backend.app.services.ingestion import (
    IngestionError,       # Custom exception raised when validating or parsing spreadsheet fails
    get_spreadsheet_sheet_names,
    inspect_spreadsheet, # Function to parse spreadsheet, build previews, and generate chunks
)
from backend.app.services.graph_service import (
    GraphServiceError,
    build_graph_from_database,
)
from backend.app.services.sql_service import DEFAULT_DATABASE

# Instantiate a FastAPI router with URL prefix '/documents' and OpenAPI documentation tag
router = APIRouter(prefix="/documents", tags=["Documents"])
logger = logging.getLogger(__name__)

# Define maximum allowed upload payload size in bytes (10 Megabytes)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Define set of allowed spreadsheet file extension types
ALLOWED_EXTENSIONS = {".csv", ".xls", ".xlsx"}


# Define POST endpoint at '/documents/upload' returning 200 OK status on success
@router.post("/upload", status_code=status.HTTP_200_OK)
async def upload_spreadsheet(file: UploadFile = File(...)):
    """Upload, inspect, chunk, and persist a spreadsheet."""
    # Extract clean filename string from upload payload (fallback to empty string if missing)
    filename = Path(file.filename or "").name
    # Extract lowercase file extension for extension validation
    extension = Path(filename).suffix.lower()

    # Reject requests with missing filenames or unsupported file extensions
    if not filename or extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,  # Return 400 Bad Request
            detail="Only .csv, .xls, and .xlsx files are supported.",
        )

    # Initialize temporary file path reference variable for cleanup in finally block
    temporary_path = None

    try:
        # Counter tracking cumulative uploaded bytes written to disk
        total_bytes = 0

        # Create a named temporary file on disk to safely stream and inspect large uploads
        with NamedTemporaryFile(
            mode="wb",           # Write binary mode
            suffix=extension,    # Preserve file extension for parser detection
            delete=False,        # Prevent auto-deletion on file close so downstream services can read it
        ) as temporary_file:
            # Store temporary file Path object
            temporary_path = Path(temporary_file.name)

            # Async loop to stream file content in 1MB chunks to avoid memory spikes
            while True:
                chunk = await file.read(1024 * 1024)  # Read 1 MB chunk asynchronously

                # Break loop when end of uploaded file stream is reached
                if not chunk:
                    break

                # Increment byte counter by current chunk size
                total_bytes += len(chunk)

                # Raise HTTP 413 Payload Too Large if byte size exceeds upper threshold limit
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,  # Return 413 Payload Too Large
                        detail="The uploaded file exceeds the 10 MB limit.",
                    )

                # Write binary chunk to disk file
                temporary_file.write(chunk)

        # Reject empty files containing zero bytes
        if total_bytes == 0:
            raise HTTPException(
                status_code=400,  # Return 400 Bad Request
                detail="The uploaded file is empty.",
            )

        sheet_names = get_spreadsheet_sheet_names(temporary_path, filename)
        inspected_sheets = [
            inspect_spreadsheet(
                temporary_path,
                filename,
                sheet_name=sheet_name,
            )
            for sheet_name in sheet_names
        ]
        stored_sheets = []
        created_dataset_ids = []
        try:
            for inspected in inspected_sheets:
                stored = store_spreadsheet(
                    file_path=temporary_path,
                    original_filename=filename,
                    chunks=inspected["chunks"],
                    sheet_name=inspected["sheet_name"],
                )
                if not stored.get("duplicate"):
                    created_dataset_ids.append(stored["dataset_id"])
                stored_sheets.append(stored)
        except DatasetStorageError:
            cleanup_errors = []
            for dataset_id in created_dataset_ids:
                try:
                    if not delete_spreadsheet(dataset_id):
                        cleanup_errors.append(dataset_id)
                except DatasetStorageError:
                    cleanup_errors.append(dataset_id)
            if cleanup_errors:
                raise DatasetStorageError(
                    "The workbook could not be stored completely, and some "
                    "partially stored sheets could not be removed."
                )
            raise

        dataset_ids = [
            item["dataset_id"]
            for item in stored_sheets
            if isinstance(item.get("dataset_id"), str)
        ]
        if dataset_ids:
            try:
                build_graph_from_database(
                    DEFAULT_DATABASE,
                    dataset_ids=dataset_ids,
                    include_evidence=True,
                )
                for item in stored_sheets:
                    item["graph_build_status"] = "complete"
                    item["graph_error"] = None
            except GraphServiceError as exc:
                logger.exception(
                    "Knowledge graph build failed after spreadsheet upload."
                )
                try:
                    update_dataset_graph_status(
                        dataset_ids,
                        "failed",
                        str(exc),
                        DEFAULT_DATABASE,
                    )
                except DatasetStorageError:
                    logger.exception(
                        "Could not persist failed graph status after upload."
                    )
                for item in stored_sheets:
                    item["graph_build_status"] = "failed"
                    item["graph_error"] = str(exc)

        stored_dataset = {
            **stored_sheets[0],
            "sheets": stored_sheets,
        }
        public_sheets = [
            {
                key: value
                for key, value in inspected.items()
                if key != "chunks"
            }
            for inspected in inspected_sheets
        ]
        preview = {
            **public_sheets[0],
            "sheets": public_sheets,
        }
        all_reused = all(item.get("duplicate") for item in stored_sheets)
        return {
            "status": "success",
            "message": (
                "This spreadsheet is already available. Existing sheets were reused."
                if all_reused
                else "Spreadsheet sheets were inspected and processed successfully."
            ),
            "data": preview,
            "storage": stored_dataset,
        }

    # Re-raise explicit HTTP exceptions without catching them as standard internal errors
    except HTTPException:
        raise

    # Invalid source files are client errors; storage failures are server errors.
    except IngestionError as exc:
        raise HTTPException(
            status_code=400,  # Return 400 Bad Request
            detail=str(exc),  # Pass specific exception message to client
        ) from exc           # Maintain full exception traceback chain
    except DatasetStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not store the uploaded dataset.",
        ) from exc

    finally:
        # Close underlying upload file handle asynchronously
        await file.close()

        # Delete temporary file from local storage if path was assigned
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
