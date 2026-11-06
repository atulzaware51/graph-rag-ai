
from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app.main import app
from backend.app.api import documents as documents_api
from backend.app.services.dataset_store import store_spreadsheet

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_upload_csv_returns_preview(tmp_path, monkeypatch):
    def store_in_test_database(
        file_path,
        original_filename,
        chunks,
        sheet_name=None,
    ):
        return store_spreadsheet(
            file_path=file_path,
            original_filename=original_filename,
            database_path=tmp_path / "test.db",
            chunks=chunks,
            sheet_name=sheet_name,
        )

    monkeypatch.setattr(
        documents_api,
        "store_spreadsheet",
        store_in_test_database,
    )

    csv_content = (
        b"product,quantity,price\n"
        b"Laptop,2,50000\n"
        b"Mouse,5,500\n"
    )

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "sales.csv",
                csv_content,
                "text/csv",
            )
        },
    )

    # assert response.status_code == 200
    assert response.status_code == 200, (
    f"Status: {response.status_code}\n"
    f"Response body: {response.text}"
)

    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["row_count"] == 2
    assert body["data"]["column_count"] == 3
    assert body["data"]["preview"][0]["product"] == "Laptop"
    assert "chunks" not in body["data"]
    assert body["storage"]["dataset_id"]
    assert body["storage"]["row_count"] == 2
    assert body["storage"]["column_count"] == 3
    assert body["storage"]["table_name"].startswith("dataset_")    


def test_upload_xlsx_exposes_each_worksheet_as_a_dataset(tmp_path, monkeypatch):
    def store_in_test_database(
        file_path,
        original_filename,
        chunks,
        sheet_name=None,
    ):
        return store_spreadsheet(
            file_path=file_path,
            original_filename=original_filename,
            database_path=tmp_path / "test.db",
            chunks=chunks,
            sheet_name=sheet_name,
        )

    monkeypatch.setattr(
        documents_api,
        "store_spreadsheet",
        store_in_test_database,
    )
    workbook = Workbook()
    products = workbook.active
    products.title = "Products"
    products.append(["product", "price"])
    products.append(["Laptop", 1200])
    employees = workbook.create_sheet("Employees")
    employees.append(["name", "department"])
    employees.append(["Alex", "Sales"])
    content = BytesIO()
    workbook.save(content)

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "analysis.xlsx",
                content.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [sheet["sheet_name"] for sheet in body["data"]["sheets"]] == [
        "Products",
        "Employees",
    ]
    assert all("chunks" not in sheet for sheet in body["data"]["sheets"])
    assert [sheet["sheet_name"] for sheet in body["storage"]["sheets"]] == [
        "Products",
        "Employees",
    ]
    assert len({
        sheet["dataset_id"] for sheet in body["storage"]["sheets"]
    }) == 2


def test_upload_rejects_unsupported_extension():
    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "notes.txt",
                b"hello",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400


def test_upload_rejects_empty_file():
    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "empty.csv",
                b"",
                "text/csv",
            )
        },
    )

    assert response.status_code == 400
