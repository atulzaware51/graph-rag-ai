import sqlite3

from backend.app.services.dataset_store import (
    delete_spreadsheet,
    get_dataset_details,
    store_spreadsheet,
)
from backend.app.services.vector_store import search_similar_chunks


def test_store_indexes_chunks_in_chroma_and_searches_selected_dataset(
    tmp_path,
    use_test_chroma_collection,
):
    source = tmp_path / "products.csv"
    source.write_text(
        "Product,Description\n"
        "Solar lamp,Rechargeable light for camping\n"
        "Desk chair,Ergonomic seating for the office\n",
        encoding="utf-8",
    )
    database = tmp_path / "vectors.db"
    stored = store_spreadsheet(
        source,
        "products.csv",
        database,
        chunks=[
            {
                "chunk_id": "solar-chunk",
                "text": "Solar lamp rechargeable camping light",
                "metadata": {"source_rows": [1]},
            },
            {
                "chunk_id": "chair-chunk",
                "text": "Ergonomic office chair",
                "metadata": {"source_rows": [2]},
            },
        ],
    )

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT chunk_id, dataset_id, model_name, dimensions, length(vector)
            FROM chunk_vectors
            ORDER BY chunk_id
            """
        ).fetchall()

    assert stored["embedding_status"] == "complete"
    assert stored["embedding_count"] == 2
    assert rows[0][1] == stored["dataset_id"]
    assert rows[0][2]
    assert rows[0][3] == 128
    assert rows[0][4] == 128 * 4
    assert {row[0] for row in rows} == {"solar-chunk", "chair-chunk"}

    results = search_similar_chunks(
        "rechargeable camping solar light",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert results[0]["chunk_id"] == "solar-chunk"
    assert results[0]["metadata"] == {"source_rows": [1]}
    assert use_test_chroma_collection.count(
        where={"dataset_id": stored["dataset_id"]}
    ) == 2


def test_vector_search_migrates_legacy_embedding_status(tmp_path):
    source = tmp_path / "legacy.csv"
    source.write_text("Product\nSolar lamp\n", encoding="utf-8")
    database = tmp_path / "legacy-vectors.db"
    stored = store_spreadsheet(
        source,
        "legacy.csv",
        database,
        chunks=[
            {
                "chunk_id": "legacy-lamp",
                "text": "Solar camping lamp",
                "metadata": {},
            }
        ],
    )
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM chunk_vectors")
        connection.execute("ALTER TABLE datasets DROP COLUMN embedding_status")

    assert search_similar_chunks(
        "solar camping",
        [stored["dataset_id"]],
        database_path=database,
    )
    details = get_dataset_details(stored["dataset_id"], database)

    assert details["embedding_status"] == "complete"
    with sqlite3.connect(database) as connection:
        vector_count = connection.execute(
            "SELECT COUNT(*) FROM chunk_vectors"
        ).fetchone()[0]
    assert vector_count == 1


def test_vector_search_never_returns_unselected_datasets(tmp_path):
    source = tmp_path / "inventory.csv"
    source.write_text("Item\nSolar lamp\n", encoding="utf-8")
    database = tmp_path / "scoped-vectors.db"
    stored = store_spreadsheet(
        source,
        "inventory.csv",
        database,
        chunks=[
            {
                "chunk_id": "lamp",
                "text": "Solar camping lamp",
                "metadata": {},
            }
        ],
    )

    assert search_similar_chunks(
        "solar camping",
        ["another-dataset"],
        database_path=database,
    ) == []


def test_vector_search_filters_results_to_selected_dataset_ids(tmp_path):
    source = tmp_path / "products.csv"
    source.write_text("Product\nSolar lamp\n", encoding="utf-8")
    database = tmp_path / "selected-vectors.db"
    selected = store_spreadsheet(
        source,
        "products.csv",
        database,
        chunks=[
            {
                "chunk_id": "selected-lamp",
                "text": "Solar lamp for camping",
                "metadata": {},
            }
        ],
    )
    other_source = tmp_path / "other-products.csv"
    other_source.write_text("Product\nSolar lamp\nCamping tent\n", encoding="utf-8")
    other = store_spreadsheet(
        other_source,
        "products.csv",
        database,
        chunks=[
            {
                "chunk_id": "other-lamp",
                "text": "Solar lamp for camping",
                "metadata": {},
            }
        ],
    )

    results = search_similar_chunks(
        "solar camping",
        [selected["dataset_id"]],
        database_path=database,
    )

    assert [result["chunk_id"] for result in results] == ["selected-lamp"]
    assert all(result["dataset_id"] != other["dataset_id"] for result in results)


def test_deleting_dataset_cascades_to_persisted_vectors(
    tmp_path,
    monkeypatch,
    use_test_chroma_collection,
):
    source = tmp_path / "products.csv"
    source.write_text("Product\nSolar lamp\n", encoding="utf-8")
    database = tmp_path / "delete-vectors.db"
    stored = store_spreadsheet(
        source,
        "products.csv",
        database,
        chunks=[
            {
                "chunk_id": "solar",
                "text": "Solar camping lamp",
                "metadata": {},
            }
        ],
    )
    search_similar_chunks(
        "solar camping",
        [stored["dataset_id"]],
        database_path=database,
    )
    monkeypatch.setattr(
        "backend.app.services.dataset_store.delete_dataset_graph",
        lambda *args, **kwargs: None,
    )

    assert delete_spreadsheet(stored["dataset_id"], database)
    with sqlite3.connect(database) as connection:
        vector_count = connection.execute(
            "SELECT COUNT(*) FROM chunk_vectors"
        ).fetchone()[0]
    assert vector_count == 0
    assert use_test_chroma_collection.count(
        where={"dataset_id": stored["dataset_id"]}
    ) == 0
