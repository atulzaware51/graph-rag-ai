
import pytest

from backend.app.services.dataset_reader import (
    DatasetNotFoundError,
    get_dataset_details,
    list_datasets,
)
from backend.app.services.dataset_store import store_spreadsheet


def test_list_datasets_returns_saved_dataset(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product Name,Quantity,Price\n"
        "Laptop,2,50000\n"
        "Mouse,5,500\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    saved = store_spreadsheet(
        csv_path, "sales.csv", database_path
    )

    result = list_datasets(database_path)

    assert len(result) == 1
    assert result[0]["dataset_id"] == saved["dataset_id"]
    assert result[0]["original_filename"] == "sales.csv"
    assert result[0]["row_count"] == 2


def test_get_dataset_details_returns_columns(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product Name,Quantity\nLaptop,2\nMouse,5\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    saved = store_spreadsheet(
        csv_path, "sales.csv", database_path
    )

    result = get_dataset_details(
        saved["dataset_id"], database_path
    )

    assert result["dataset_id"] == saved["dataset_id"]
    assert len(result["columns"]) == 2
    assert result["columns"][0]["original_name"] == "Product Name"
    assert result["columns"][0]["sql_name"] == "product_name"


def test_get_dataset_details_rejects_unknown_id(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "product\nLaptop\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    store_spreadsheet(csv_path, "sales.csv", database_path)

    with pytest.raises(DatasetNotFoundError, match="was not found"):
        get_dataset_details("unknown-dataset-id", database_path)


def test_list_datasets_returns_empty_for_missing_database(tmp_path):
    result = list_datasets(tmp_path / "missing.db")

    assert result == []
