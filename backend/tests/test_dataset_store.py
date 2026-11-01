
import json
import sqlite3

import pytest
from openpyxl import Workbook
import xlwt

from backend.app.services.dataset_store import (
    DatasetStorageError,
    delete_spreadsheet,
    get_dataset_chunks,
    store_spreadsheet,
)
from backend.app.services.conversation_store import (
    ConversationStoreError,
    create_conversation,
    get_conversation,
    record_sql,
)
from backend.app.services import dataset_store
from backend.app.services.graph_store import GraphPersistenceError


def test_store_csv_and_metadata(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product Name,Quantity,Price\n"
        "Laptop,2,50000\n"
        "Mouse,5,500\n",
        encoding="utf-8",
    )

    database_path = tmp_path / "test.db"

    result = store_spreadsheet(
        csv_path,
        "sales.csv",
        database_path,
    )

    assert result["filename"] == "sales.csv"
    assert result["row_count"] == 2
    assert result["column_count"] == 3
    assert result["columns"][0]["original_name"] == "Product Name"
    assert result["columns"][0]["sql_name"] == "product_name"
    assert result["chunk_count"] == 0

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            f'SELECT product_name, quantity, price '
            f'FROM "{result["table_name"]}" ORDER BY rowid'
        ).fetchall()

        metadata = connection.execute(
            "SELECT row_count, column_count FROM datasets "
            "WHERE dataset_id = ?",
            (result["dataset_id"],),
        ).fetchone()

    assert rows == [("Laptop", 2, 50000), ("Mouse", 5, 500)]
    assert metadata == (2, 3)
    assert len(result["content_fingerprint"]) == 64


def test_content_fingerprint_matches_repeated_uploads(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    contents = "Customer ID,Name\n1001,Alice\n1002,Bob\n"
    first.write_text(contents, encoding="utf-8")
    second.write_text(contents, encoding="utf-8")

    database = tmp_path / "test.db"
    first_upload = store_spreadsheet(first, "customers.csv", database)
    second_upload = store_spreadsheet(second, "customers.csv", database)

    assert first_upload["dataset_id"] != second_upload["dataset_id"]
    assert (
        first_upload["content_fingerprint"]
        == second_upload["content_fingerprint"]
    )


def test_content_fingerprint_matches_same_sheet_across_file_formats(tmp_path):
    csv_path = tmp_path / "customers.csv"
    csv_path.write_text(
        "Customer ID,Name\n1001,Alice\n1002,Bob\n",
        encoding="utf-8",
    )

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Customer ID", "Name"])
    worksheet.append([1001, "Alice"])
    worksheet.append([1002, "Bob"])
    xlsx_path = tmp_path / "customers.xlsx"
    workbook.save(xlsx_path)

    database = tmp_path / "test.db"
    csv_upload = store_spreadsheet(csv_path, "customers.csv", database)
    xlsx_upload = store_spreadsheet(xlsx_path, "customers.xlsx", database)

    assert (
        csv_upload["content_fingerprint"]
        == xlsx_upload["content_fingerprint"]
    )


def test_store_legacy_xls_and_metadata(tmp_path):
    workbook = xlwt.Workbook()
    worksheet = workbook.add_sheet("Sales")
    for row_index, row in enumerate(
        [["Product Name", "Quantity"], ["Laptop", 2], ["Mouse", 5]]
    ):
        for column_index, value in enumerate(row):
            worksheet.write(row_index, column_index, value)
    xls_path = tmp_path / "sales.xls"
    workbook.save(str(xls_path))

    result = store_spreadsheet(xls_path, "sales.xls", tmp_path / "test.db")

    assert result["filename"] == "sales.xls"
    assert result["row_count"] == 2
    assert result["column_count"] == 2
    assert result["columns"][0]["sql_name"] == "product_name"
    with sqlite3.connect(tmp_path / "test.db") as connection:
        rows = connection.execute(
            f'SELECT product_name, quantity FROM "{result["table_name"]}" '
            "ORDER BY rowid"
        ).fetchall()
    assert rows == [("Laptop", 2), ("Mouse", 5)]


def test_content_fingerprint_changes_when_cell_values_change(tmp_path):
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    first.write_text("Customer ID\n1001\n", encoding="utf-8")
    second.write_text("Customer ID\n1002\n", encoding="utf-8")

    database = tmp_path / "test.db"
    first_upload = store_spreadsheet(first, "customers.csv", database)
    second_upload = store_spreadsheet(second, "customers.csv", database)

    assert (
        first_upload["content_fingerprint"]
        != second_upload["content_fingerprint"]
    )


def test_store_rejects_unsupported_extension(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("not a spreadsheet", encoding="utf-8")

    with pytest.raises(DatasetStorageError, match="Unsupported file type"):
        store_spreadsheet(
            file_path,
            "notes.txt",
            tmp_path / "test.db",
        )


def test_store_rejects_missing_file(tmp_path):
    with pytest.raises(DatasetStorageError, match="could not be found"):
        store_spreadsheet(
            tmp_path / "missing.csv",
            "missing.csv",
            tmp_path / "test.db",
        )


def test_store_creates_unique_safe_column_names(tmp_path):
    csv_path = tmp_path / "columns.csv"
    csv_path.write_text(
        "Customer Name,Customer-Name,123,!!!\n"
        "A,B,10,ok\n",
        encoding="utf-8",
    )

    result = store_spreadsheet(
        csv_path,
        "columns.csv",
        tmp_path / "test.db",
    )

    names = [column["sql_name"] for column in result["columns"]]

    assert names == [
        "customer_name",
        "customer_name_2",
        "column_123",
        "column_4",
    ]
    assert len(names) == len(set(names))


def test_store_and_retrieve_chunks(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product,Quantity\nLaptop,2\nMouse,5\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    chunks = [
        {
            "chunk_id": "chunk-001",
            "text": "Product Laptop has quantity 2.",
            "metadata": {
                "source_rows": [0],
                "dataset_name": "sales.csv",
            },
        },
        {
            "chunk_id": "chunk-002",
            "text": "Product Mouse has quantity 5.",
            "metadata": {
                "source_rows": [1],
                "dataset_name": "sales.csv",
            },
        },
    ]

    result = store_spreadsheet(
        csv_path,
        "sales.csv",
        database_path,
        chunks=chunks,
    )

    assert result["chunk_count"] == 2

    retrieved = get_dataset_chunks(
        result["dataset_id"],
        database_path,
    )

    assert len(retrieved) == 2
    assert retrieved[0]["chunk_id"] == "chunk-001"
    assert retrieved[0]["chunk_index"] == 0
    assert retrieved[0]["text"] == "Product Laptop has quantity 2."
    assert retrieved[0]["metadata"] == {
        "source_rows": [0],
        "dataset_name": "sales.csv",
    }
    assert retrieved[1]["chunk_id"] == "chunk-002"
    assert retrieved[1]["chunk_index"] == 1


def test_store_rejects_invalid_chunk_before_saving(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product,Quantity\nLaptop,2\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    with pytest.raises(DatasetStorageError, match="non-empty text"):
        store_spreadsheet(
            csv_path,
            "sales.csv",
            database_path,
            chunks=[
                {
                    "chunk_id": "invalid",
                    "text": "   ",
                    "metadata": {},
                }
            ],
        )

    # Validation should fail before creating the database.
    assert not database_path.exists()


def test_chunk_metadata_is_stored_as_json(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product\nLaptop\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"

    result = store_spreadsheet(
        csv_path,
        "sales.csv",
        database_path,
        chunks=[
            {
                "chunk_id": "chunk-json",
                "text": "Product is Laptop.",
                "metadata": {"source_rows": [0]},
            }
        ],
    )

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute(
            """
            SELECT metadata_json
            FROM document_chunks
            WHERE dataset_id = ?
            """,
            (result["dataset_id"],),
        ).fetchone()

    assert stored is not None
    assert json.loads(stored[0]) == {"source_rows": [0]}


def test_retrieve_chunks_from_missing_database_raises_error(tmp_path):
    with pytest.raises(
        DatasetStorageError,
        match="database could not be found",
    ):
        get_dataset_chunks(
            "nonexistent-dataset",
            tmp_path / "missing.db",
        )


def test_delete_spreadsheet_removes_dataset_data_and_associated_history(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "Product,Quantity\nLaptop,2\n",
        encoding="utf-8",
    )
    database_path = tmp_path / "test.db"
    stored = store_spreadsheet(
        csv_path,
        "sales.csv",
        database_path,
        chunks=[
            {
                "chunk_id": "sales-chunk",
                "text": "Laptop has quantity 2.",
                "metadata": {},
            }
        ],
    )
    associated_chat = create_conversation(
        title="Sales query",
        dataset_id=stored["dataset_id"],
        database_path=database_path,
    )
    metadata_linked_chat = create_conversation(
        title="Another sales query",
        database_path=database_path,
    )
    unrelated_chat = create_conversation(
        title="Other dataset",
        dataset_id="other-dataset",
        database_path=database_path,
    )
    record_sql(
        metadata_linked_chat["conversation_id"],
        actor="assistant",
        sql="SELECT * FROM sales",
        metadata={"dataset_id": stored["dataset_id"]},
        database_path=database_path,
    )

    assert delete_spreadsheet(stored["dataset_id"], database_path)
    assert not delete_spreadsheet(stored["dataset_id"], database_path)

    with sqlite3.connect(database_path) as connection:
        remaining_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        dataset_records = connection.execute(
            "SELECT COUNT(*) FROM datasets WHERE dataset_id = ?",
            (stored["dataset_id"],),
        ).fetchone()[0]
        chunk_records = connection.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE dataset_id = ?",
            (stored["dataset_id"],),
        ).fetchone()[0]

    assert stored["table_name"] not in remaining_tables
    assert dataset_records == 0
    assert chunk_records == 0
    assert get_conversation(
        unrelated_chat["conversation_id"],
        database_path=database_path,
    )["title"] == "Other dataset"
    with pytest.raises(ConversationStoreError, match="not found"):
        get_conversation(
            associated_chat["conversation_id"],
            database_path=database_path,
        )
    with pytest.raises(ConversationStoreError, match="not found"):
        get_conversation(
            metadata_linked_chat["conversation_id"],
            database_path=database_path,
        )


def test_persistent_graph_failure_preserves_dataset_sqlite_records(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "customers.csv"
    source.write_text("customer_id,name\n1,Ada\n", encoding="utf-8")
    database_path = tmp_path / "datasets.db"
    stored = store_spreadsheet(source, "customers.csv", database_path)
    failure = GraphPersistenceError("database unavailable")
    monkeypatch.setattr(
        dataset_store,
        "delete_dataset_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(DatasetStorageError, match="persistent knowledge graph"):
        delete_spreadsheet(stored["dataset_id"], database_path)

    with sqlite3.connect(database_path) as connection:
        dataset = connection.execute(
            "SELECT table_name FROM datasets WHERE dataset_id = ?",
            (stored["dataset_id"],),
        ).fetchone()
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (stored["table_name"],),
        ).fetchone()
    assert dataset == (stored["table_name"],)
    assert table_exists is not None


def test_dataset_deletion_does_not_depend_on_neo4j(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "customers.csv"
    source.write_text("customer_id,name\n1,Ada\n", encoding="utf-8")
    database_path = tmp_path / "datasets.db"
    stored = store_spreadsheet(source, "customers.csv", database_path)
    deleted_graph_ids = []
    monkeypatch.setattr(
        dataset_store,
        "delete_dataset_graph",
        lambda dataset_id, **_kwargs: deleted_graph_ids.append(dataset_id),
    )

    assert delete_spreadsheet(stored["dataset_id"], database_path)
    assert deleted_graph_ids == [stored["dataset_id"]]
