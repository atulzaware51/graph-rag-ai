
import zipfile

import pandas as pd
import pytest
import xlwt

from backend.app.services.ingestion import (
    IngestionError,
    get_spreadsheet_sheet_names,
    inspect_spreadsheet,
)


def test_inspect_csv_returns_metadata_and_preview(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "product,quantity,price\n"
        "Laptop,2,50000\n"
        "Mouse,5,500\n",
        encoding="utf-8",
    )

    result = inspect_spreadsheet(csv_path, "sales.csv")

    assert result["filename"] == "sales.csv"
    assert result["file_type"] == "csv"
    assert result["row_count"] == 2
    assert result["column_count"] == 3
    assert len(result["preview"]) == 2
    assert result["preview"][0]["product"] == "Laptop"


def test_inspect_excel_returns_metadata(tmp_path):
    excel_path = tmp_path / "sales.xlsx"

    dataframe = pd.DataFrame(
        {
            "product": ["Laptop", "Mouse"],
            "quantity": [2, 5],
        }
    )
    dataframe.to_excel(excel_path, index=False, engine="openpyxl")

    result = inspect_spreadsheet(excel_path, "sales.xlsx")

    assert result["row_count"] == 2
    assert result["column_count"] == 2
    assert result["file_type"] == "xlsx"
    assert result["sheet_name"] == "Sheet1"


def test_inspect_all_workbook_sheets(tmp_path):
    excel_path = tmp_path / "workbook.xlsx"
    workbook = pd.ExcelWriter(excel_path, engine="openpyxl")
    pd.DataFrame({"product": ["Laptop"]}).to_excel(
        workbook,
        sheet_name="Products",
        index=False,
    )
    pd.DataFrame({"department": ["Sales"]}).to_excel(
        workbook,
        sheet_name="Employees",
        index=False,
    )
    workbook.close()

    sheet_names = get_spreadsheet_sheet_names(excel_path, "workbook.xlsx")
    inspected = [
        inspect_spreadsheet(excel_path, "workbook.xlsx", name)
        for name in sheet_names
    ]

    assert sheet_names == ["Products", "Employees"]
    assert [sheet["sheet_name"] for sheet in inspected] == sheet_names
    assert inspected[0]["preview"][0]["product"] == "Laptop"
    assert inspected[1]["preview"][0]["department"] == "Sales"


def test_inspect_legacy_xls_returns_metadata(tmp_path):
    excel_path = tmp_path / "sales.xls"
    workbook = xlwt.Workbook()
    worksheet = workbook.add_sheet("Sales")
    rows = [
        ["product", "quantity"],
        ["Laptop", 2],
        ["Mouse", 5],
    ]
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            worksheet.write(row_index, column_index, value)
    workbook.save(str(excel_path))

    result = inspect_spreadsheet(excel_path, "sales.xls")

    assert result["row_count"] == 2
    assert result["column_count"] == 2
    assert result["file_type"] == "xls"
    assert result["preview"][0]["product"] == "Laptop"


def test_rejects_invalid_xls_content(tmp_path):
    excel_path = tmp_path / "invalid.xls"
    excel_path.write_bytes(b"not an XLS workbook")

    with pytest.raises(IngestionError, match="Could not parse the XLS"):
        inspect_spreadsheet(excel_path, "invalid.xls")


def test_rejects_unsupported_file_type(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("Not a spreadsheet", encoding="utf-8")

    with pytest.raises(IngestionError, match="Unsupported file type"):
        inspect_spreadsheet(file_path, "notes.txt")


def test_rejects_missing_file(tmp_path):
    missing_path = tmp_path / "missing.csv"

    with pytest.raises(IngestionError, match="could not be found"):
        inspect_spreadsheet(missing_path, "missing.csv")


def test_rejects_empty_file(tmp_path):
    empty_path = tmp_path / "empty.csv"
    empty_path.write_bytes(b"")

    with pytest.raises(IngestionError, match="empty"):
        inspect_spreadsheet(empty_path, "empty.csv")


def test_rejects_file_over_size_limit(tmp_path):
    file_path = tmp_path / "large.csv"
    file_path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))

    with pytest.raises(IngestionError, match="10 MB"):
        inspect_spreadsheet(file_path, "large.csv")


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("name,name\nAlice,Bob\n", "Duplicate column headers"),
        ("name,,value\nAlice,1,2\n", "must not be empty"),
    ],
)
def test_rejects_invalid_csv_headers(tmp_path, contents, message):
    csv_path = tmp_path / "invalid.csv"
    csv_path.write_text(contents, encoding="utf-8")

    with pytest.raises(IngestionError, match=message):
        inspect_spreadsheet(csv_path, "invalid.csv")


def test_rejects_csv_without_data_rows(tmp_path):
    csv_path = tmp_path / "header-only.csv"
    csv_path.write_text("name,value\n", encoding="utf-8")

    with pytest.raises(IngestionError, match="no data rows"):
        inspect_spreadsheet(csv_path, "header-only.csv")


def test_rejects_non_xlsx_content_with_xlsx_extension(tmp_path):
    excel_path = tmp_path / "not-a-workbook.xlsx"
    excel_path.write_bytes(b"not an XLSX file")

    with pytest.raises(IngestionError, match="not a valid spreadsheet"):
        inspect_spreadsheet(excel_path, "not-a-workbook.xlsx")


def test_rejects_corrupted_xlsx_container(tmp_path):
    excel_path = tmp_path / "corrupted.xlsx"
    with zipfile.ZipFile(excel_path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook")

    with pytest.raises(IngestionError, match="Could not parse the XLSX"):
        inspect_spreadsheet(excel_path, "corrupted.xlsx")


def test_rejects_csv_above_row_limit_before_loading_dataframe(tmp_path):
    csv_path = tmp_path / "too-many-rows.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        output.write("value\n")
        output.writelines("1\n" for _ in range(100_001))

    with pytest.raises(IngestionError, match="supported row limit"):
        inspect_spreadsheet(csv_path, "too-many-rows.csv")
