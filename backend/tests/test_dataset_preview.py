import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.api import datasets as datasets_api
from backend.app.main import app
from backend.app.services.dataset_reader import (
    DatasetNotFoundError,
    get_dataset_preview,
)
from backend.app.services.dataset_store import store_spreadsheet


client = TestClient(app)


def test_dataset_preview_reads_only_registered_table(tmp_path):
    source = tmp_path / "sample.csv"
    source.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")
    database = tmp_path / "datasets.db"
    dataset = store_spreadsheet(source, "sample.csv", database)

    rows = get_dataset_preview(
        dataset["dataset_id"],
        limit=1,
        database_path=database,
    )

    assert rows == [{"name": "alpha", "value": 1}]


def test_dataset_preview_rejects_unknown_dataset(tmp_path):
    database = tmp_path / "datasets.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                table_name TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                column_count INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    with pytest.raises(DatasetNotFoundError):
        get_dataset_preview("a" * 32, database_path=database)


def test_preview_api_returns_rows_and_validates_limit(monkeypatch):
    monkeypatch.setattr(
        datasets_api,
        "get_dataset_preview",
        lambda dataset_id, limit: [{"name": "alpha"}],
    )

    response = client.get(f"/datasets/{'a' * 32}/preview?limit=5")
    assert response.status_code == 200
    assert response.json()["rows"] == [{"name": "alpha"}]

    response = client.get(f"/datasets/{'a' * 32}/preview?limit=101")
    assert response.status_code == 422
