
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.dataset_reader import DatasetNotFoundError
from backend.app.services.relationship_inference import (
    RelationshipInferenceError,
)

client = TestClient(app)


def test_schema_endpoint_returns_profile(monkeypatch):
    def fake_profile(dataset_id):
        return {
            "dataset_id": dataset_id,
            "row_count": 3,
            "column_count": 1,
            "columns": [
                {
                    "name": "student_id",
                    "null_count": 0,
                    "unique_count": 3,
                    "sample_values": [101, 102, 103],
                    "is_potential_key": True,
                }
            ],
        }

    monkeypatch.setattr(
        "backend.app.api.datasets.profile_dataset",
        fake_profile,
    )

    response = client.get("/datasets/students-1/schema")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["schema"]["row_count"] == 3
    assert response.json()["schema"]["columns"][0]["is_potential_key"]


def test_schema_endpoint_returns_404_for_unknown_dataset(monkeypatch):
    def fake_profile(dataset_id):
        raise DatasetNotFoundError("Dataset not found")

    monkeypatch.setattr(
        "backend.app.api.datasets.profile_dataset",
        fake_profile,
    )

    response = client.get("/datasets/unknown-id/schema")

    assert response.status_code == 404


def test_relationships_endpoint_returns_candidates(monkeypatch):
    def fake_inference():
        return {
            "dataset_count": 2,
            "relationship_count": 1,
            "relationships": [
                {
                    "confidence": 0.95,
                    "status": "candidate",
                }
            ],
        }

    monkeypatch.setattr(
        "backend.app.api.relationships.infer_relationships",
        fake_inference,
    )

    response = client.get("/relationships/")

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["relationship_count"] == 1
    assert (
        response.json()["relationships"][0]["status"]
        == "candidate"
    )


def test_relationships_endpoint_handles_service_error(monkeypatch):
    def fake_inference():
        raise RelationshipInferenceError("Test database error")

    monkeypatch.setattr(
        "backend.app.api.relationships.infer_relationships",
        fake_inference,
    )

    response = client.get("/relationships/")

    assert response.status_code == 500
    assert response.json()["detail"] == "Could not infer dataset relationships."
