
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api import documents as documents_api
from backend.app.services.dataset_store import store_spreadsheet

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_upload_csv_returns_preview(tmp_path, monkeypatch):
    def store_in_test_database(file_path, original_filename, chunks):
        return store_spreadsheet(
            file_path=file_path,
            original_filename=original_filename,
            database_path=tmp_path / "test.db",
            chunks=chunks,
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
    assert body["storage"]["dataset_id"]
    assert body["storage"]["row_count"] == 2
    assert body["storage"]["column_count"] == 3
    assert body["storage"]["table_name"].startswith("dataset_")    


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
