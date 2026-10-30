from fastapi.testclient import TestClient

from backend.app.api import datasets as datasets_api
from backend.app.main import app


client = TestClient(app)


def test_delete_dataset_returns_deletion_status(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "delete_spreadsheet",
        lambda dataset_id: dataset_id == "a" * 32,
    )

    response = client.delete(f"/datasets/{'a' * 32}")

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "dataset_id": "a" * 32,
        "deleted": True,
    }


def test_delete_dataset_returns_not_found(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "delete_spreadsheet",
        lambda dataset_id: False,
    )

    response = client.delete(f"/datasets/{'b' * 32}")

    assert response.status_code == 404
