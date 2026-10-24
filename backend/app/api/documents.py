# Import Path class for object-oriented filesystem path manipulation
from pathlib import Path
# Import NamedTemporaryFile utility to create disk-backed temporary files
from tempfile import NamedTemporaryFile

# Import FastAPI routing, file upload, status codes, and HTTP exception handling utilities
from fastapi import APIRouter, File, HTTPException, UploadFile, status

# Import dataset storage error class and store function from database storage service
from backend.app.services.dataset_store import (
    DatasetStorageError,  # Custom exception raised when storing data or chunks fails
    store_spreadsheet,    # Function to save dataset rows, metadata, and chunks into SQLite
)
# Import spreadsheet ingestion error class and inspector function from ingestion service
from backend.app.services.ingestion import (
    IngestionError,       # Custom exception raised when validating or parsing spreadsheet fails
    inspect_spreadsheet, # Function to parse spreadsheet, build previews, and generate chunks
)

# Instantiate a FastAPI router with URL prefix '/documents' and OpenAPI documentation tag
router = APIRouter(prefix="/documents", tags=["Documents"])

# Define maximum allowed upload payload size in bytes (10 Megabytes)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Define set of allowed spreadsheet file extension types
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


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
            detail="Only .csv and .xlsx files are supported.",
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

        # Step 1: Parse and inspect the spreadsheet file to extract metadata, preview rows, and text chunks
        preview = inspect_spreadsheet(temporary_path, filename)

        # Step 2: Persist the spreadsheet data, column schema, and generated text chunks into SQLite
        stored_dataset = store_spreadsheet(
            file_path=temporary_path,
            original_filename=filename,
            chunks=preview["chunks"],
        )

        # Return successful response payload containing inspection preview and database storage metadata
        return {
            "status": "success",
            "message": "Spreadsheet inspected, chunked, and saved successfully.",
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