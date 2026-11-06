"""Validate spreadsheet uploads, profile their contents, and prepare evidence chunks."""

# Import Path class for object-oriented filesystem path operations
from pathlib import Path
# Import Any for general typing support
from typing import Any
import csv
import struct
import zipfile
from xml.etree.ElementTree import ParseError as XMLParseError

# Import pandas library for tabular data manipulation and spreadsheet parsing
import pandas as pd
import xlrd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

# Import custom error and chunking function from backend chunking service module
from backend.app.services.chunking import (
    ChunkingError,       # Custom exception raised when text chunking fails
    rows_to_text_chunks, # Function that converts spreadsheet rows into text chunks
)


# Define set of allowed spreadsheet file extensions
SUPPORTED_EXTENSIONS = {".csv", ".xls", ".xlsx"}
# Define maximum allowed file size limit in bytes (10 Megabytes)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_SPREADSHEET_ROWS = 100_000
MAX_SPREADSHEET_COLUMNS = 500
MAX_WORKSHEETS = 20
# Define maximum number of rows to return in the preview dataset
MAX_PREVIEW_ROWS = 10
# Define default character capacity limit per chunk
DEFAULT_CHUNK_SIZE = 1000
# Define default character overlap size between consecutive chunks
DEFAULT_CHUNK_OVERLAP = 150


# Define custom exception class inheriting from Exception for spreadsheet ingestion failures
class IngestionError(Exception):
    """Raised when a spreadsheet cannot be safely ingested."""


def _validate_file_and_headers(
    path: Path,
    extension: str,
    sheet_name: str | None = None,
) -> None:
    """Validate spreadsheet structure and its original header values."""

    if extension == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.reader(source, strict=True)
                headers = next(reader, None)
                for row_number, row in enumerate(reader, start=1):
                    if row_number > MAX_SPREADSHEET_ROWS:
                        raise IngestionError(
                            "The spreadsheet exceeds the supported row limit."
                        )
                    if len(row) > MAX_SPREADSHEET_COLUMNS:
                        raise IngestionError(
                            "The spreadsheet exceeds the supported column limit."
                        )
        except (csv.Error, UnicodeDecodeError, OSError) as exc:
            raise IngestionError(
                "Could not parse the CSV file. Check its encoding and format."
            ) from exc
    elif extension == ".xls":
        workbook = None
        try:
            workbook = xlrd.open_workbook(
                str(path),
                on_demand=True,
            )
            worksheet = (
                workbook.sheet_by_name(sheet_name)
                if sheet_name
                else workbook.sheet_by_index(0)
            )
            if worksheet.nrows > MAX_SPREADSHEET_ROWS + 1:
                raise IngestionError(
                    "The spreadsheet exceeds the supported row limit."
                )
            if worksheet.ncols > MAX_SPREADSHEET_COLUMNS:
                raise IngestionError(
                    "The spreadsheet exceeds the supported column limit."
                )
            headers = worksheet.row_values(0) if worksheet.nrows else None
        except IngestionError:
            raise
        except (
            OSError,
            ValueError,
            TypeError,
            IndexError,
            struct.error,
            xlrd.XLRDError,
        ) as exc:
            raise IngestionError(
                "Could not parse the XLS file. Check its format and headers."
            ) from exc
        finally:
            if workbook is not None:
                workbook.release_resources()
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                member_names = {member.filename for member in members}
                expanded_size = sum(member.file_size for member in members)
                if (
                    expanded_size > MAX_XLSX_UNCOMPRESSED_BYTES
                    or len(members) > 10_000
                    or "[Content_Types].xml" not in member_names
                    or "xl/workbook.xml" not in member_names
                ):
                    raise IngestionError(
                        "The XLSX file is invalid or exceeds safe size limits."
                    )
                if any(
                    member.filename.lower().endswith("vbaproject.bin")
                    for member in members
                ):
                    raise IngestionError(
                        "Macro-enabled spreadsheets are not supported."
                    )
        except zipfile.BadZipFile as exc:
            raise IngestionError(
                "The XLSX file is not a valid spreadsheet."
            ) from exc

        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                worksheet = (
                    workbook[sheet_name]
                    if sheet_name
                    else workbook.active
                )
                if (
                    worksheet.max_row is not None
                    and worksheet.max_row > MAX_SPREADSHEET_ROWS + 1
                ):
                    raise IngestionError(
                        "The spreadsheet exceeds the supported row limit."
                    )
                if (
                    worksheet.max_column is not None
                    and worksheet.max_column > MAX_SPREADSHEET_COLUMNS
                ):
                    raise IngestionError(
                        "The spreadsheet exceeds the supported column limit."
                    )
                headers = next(
                    worksheet.iter_rows(
                        min_row=1,
                        max_row=1,
                        values_only=True,
                    ),
                    None,
                )
            finally:
                workbook.close()
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            InvalidFileException,
            XMLParseError,
            zipfile.BadZipFile,
        ) as exc:
            raise IngestionError(
                "Could not parse the XLSX file. Check its format and headers."
            ) from exc

    if not headers or len(headers) > MAX_SPREADSHEET_COLUMNS:
        raise IngestionError(
            "The spreadsheet must contain between 1 and "
            f"{MAX_SPREADSHEET_COLUMNS} column headers."
        )

    normalized_headers = []
    for header in headers:
        if header is None or not str(header).strip():
            raise IngestionError("Column headers must not be empty.")
        normalized_headers.append(str(header).strip().casefold())

    if len(normalized_headers) != len(set(normalized_headers)):
        raise IngestionError("Duplicate column headers are not supported.")


# Main function to validate, parse, preview, and chunk an uploaded spreadsheet file
def inspect_spreadsheet(
    file_path: str | Path,
    original_filename: str,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    """Validate, inspect, preview, and chunk a CSV or Excel spreadsheet."""

    # Convert incoming file path string/object to a standardized Path object
    path = Path(file_path)
    # Extract file extension from original filename in lowercase for validation
    extension = Path(original_filename).suffix.lower()

    # Check if file extension is present in the supported extensions set
    if extension not in SUPPORTED_EXTENSIONS:
        raise IngestionError(
            "Unsupported file type. Upload a .csv, .xls, or .xlsx file."
        )

    # Verify that the target path exists and points to a regular file
    if not path.exists() or not path.is_file():
        raise IngestionError("The uploaded file could not be found.")

    # Fetch file size in bytes from OS file metadata
    file_size = path.stat().st_size

    # Reject empty (0-byte) uploaded files
    if file_size == 0:
        raise IngestionError("The uploaded file is empty.")

    # Enforce file size upper bound limit
    if file_size > MAX_FILE_SIZE_BYTES:
        raise IngestionError("The file exceeds the 10 MB size limit.")

    if extension == ".csv" and sheet_name is not None:
        raise IngestionError("CSV files do not contain named worksheets.")
    if extension != ".csv" and sheet_name is None:
        sheet_name = get_spreadsheet_sheet_names(path, original_filename)[0]

    _validate_file_and_headers(path, extension, sheet_name)

    try:
        # Read CSV file using pandas default engine
        if extension == ".csv":
            dataframe = pd.read_csv(path)
        # Read Excel spreadsheets with the engine that matches their format.
        elif extension == ".xls":
            dataframe = pd.read_excel(
                path,
                sheet_name=sheet_name or 0,
                engine="xlrd",
            )
        else:
            dataframe = pd.read_excel(
                path,
                sheet_name=sheet_name or 0,
                engine="openpyxl",
            )
    except (
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
        UnicodeDecodeError,
        ValueError,
        OSError,
        IndexError,
        TypeError,
        AttributeError,
        struct.error,
        xlrd.XLRDError,
        InvalidFileException,
        XMLParseError,
        zipfile.BadZipFile,
    ) as exc:
        raise IngestionError(
            "Could not parse the spreadsheet. Check its format and headers."
        ) from exc

    # Ensure dataframe has at least one column header/data field
    if len(dataframe.columns) == 0:
        raise IngestionError(
            "The spreadsheet has no column headers or data."
        )

    if dataframe.empty:
        raise IngestionError("The spreadsheet contains no data rows.")

    if (
        len(dataframe) > MAX_SPREADSHEET_ROWS
        or len(dataframe.columns) > MAX_SPREADSHEET_COLUMNS
    ):
        raise IngestionError(
            "The spreadsheet exceeds the supported row or column limit."
        )

    # Convert all DataFrame column headers to explicit strings to ensure consistent indexing
    dataframe.columns = [str(column) for column in dataframe.columns]

    # Generate metadata list describing each column's name, data type, and missing value count
    columns = [
        {
            "name": str(column),                             # Column header name
            "data_type": str(dataframe[column].dtype),       # Data type string representation
            "null_count": int(dataframe[column].isna().sum()), # Total missing/NaN count
        }
        for column in dataframe.columns
    ]

    # Extract top N rows for preview and cast values to general Python objects
    preview_dataframe = dataframe.head(MAX_PREVIEW_ROWS).astype(object)
    # Replace NaN/NaN-like missing values with standard Python None objects
    preview_dataframe = preview_dataframe.where(
        pd.notna(preview_dataframe), None
    )

    # Convert preview dataframe rows to a list of dictionary objects with string keys
    preview = [
        {str(key): value for key, value in row.items()}
        for row in preview_dataframe.to_dict(orient="records")
    ]

    # Normalize entire dataframe by converting values to Python objects and replacing NaNs with None
    normalized_dataframe = dataframe.astype(object).where(
        pd.notna(dataframe), None
    )
    # Export full dataset rows as dictionary records for chunking processing
    records = normalized_dataframe.to_dict(orient="records")

    try:
        # Chunk row records into text chunks using chunking service utility
        chunks = rows_to_text_chunks(
            rows=records,
            dataset_name=(
                f"{Path(original_filename).name} - {sheet_name}"
                if sheet_name
                else Path(original_filename).name
            ),
            chunk_size=DEFAULT_CHUNK_SIZE,
            overlap=DEFAULT_CHUNK_OVERLAP,
        )
    # Catch chunking validation errors and convert to IngestionError
    except ChunkingError as exc:
        raise IngestionError(
            f"Could not generate spreadsheet chunks: {exc}"
        ) from exc

    # Return structured dict summarizing inspection metadata, preview rows, and generated chunks
    return {
        "filename": Path(original_filename).name,   # Original file name string
        "sheet_name": sheet_name,
        "file_type": extension.lstrip("."),        # Extension without leading dot (e.g., 'csv')
        "row_count": int(len(dataframe)),          # Total data row count
        "column_count": int(len(dataframe.columns)),# Total column count
        "columns": columns,                        # Column schema details
        "preview": preview,                        # Sample preview row records
        "chunk_count": len(chunks),                # Total number of generated text chunks
        "chunks": chunks,                          # List of generated text chunk dicts
    }


def get_spreadsheet_sheet_names(
    file_path: str | Path,
    original_filename: str,
) -> list[str | None]:
    """Return worksheet names after validating the workbook container."""
    path = Path(file_path)
    extension = Path(original_filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise IngestionError(
            "Unsupported file type. Upload a .csv, .xls, or .xlsx file."
        )
    if not path.is_file():
        raise IngestionError("The uploaded file could not be found.")
    if path.stat().st_size == 0:
        raise IngestionError("The uploaded file is empty.")
    if path.stat().st_size > MAX_FILE_SIZE_BYTES:
        raise IngestionError("The file exceeds the 10 MB size limit.")
    if extension == ".csv":
        return [None]

    if extension == ".xls":
        workbook = None
        try:
            workbook = xlrd.open_workbook(str(path), on_demand=True)
            names = list(workbook.sheet_names())
        except (
            OSError,
            ValueError,
            TypeError,
            struct.error,
            xlrd.XLRDError,
        ) as exc:
            raise IngestionError(
                "Could not parse the XLS file. Check its format and headers."
            ) from exc
        finally:
            if workbook is not None:
                workbook.release_resources()
    else:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                member_names = {member.filename for member in members}
                if (
                    sum(member.file_size for member in members)
                    > MAX_XLSX_UNCOMPRESSED_BYTES
                    or len(members) > 10_000
                    or "[Content_Types].xml" not in member_names
                    or "xl/workbook.xml" not in member_names
                    or any(
                        member.filename.lower().endswith("vbaproject.bin")
                        for member in members
                    )
                ):
                    raise IngestionError(
                        "The XLSX file is invalid or exceeds safe size limits."
                    )
            workbook = load_workbook(path, read_only=True, data_only=True)
            try:
                names = list(workbook.sheetnames)
            finally:
                workbook.close()
        except IngestionError:
            raise
        except zipfile.BadZipFile as exc:
            raise IngestionError(
                "The XLSX file is not a valid spreadsheet."
            ) from exc
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            InvalidFileException,
            XMLParseError,
            zipfile.BadZipFile,
        ) as exc:
            raise IngestionError(
                "Could not parse the XLSX file. Check its format and headers."
            ) from exc

    if not names or len(names) > MAX_WORKSHEETS:
        raise IngestionError(
            f"Workbooks must contain between 1 and {MAX_WORKSHEETS} worksheets."
        )
    return names