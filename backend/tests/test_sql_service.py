import sqlite3

import pytest

from backend.app.services.sql_service import (
    SQLServiceError,
    execute_readonly_sql,
)


@pytest.fixture
def test_database(tmp_path):
    database = tmp_path / "test.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE sales (product TEXT, quantity INTEGER, price REAL)"
    )
    connection.executemany(
        "INSERT INTO sales VALUES (?, ?, ?)",
        [
            ("Laptop", 2, 50000),
            ("Mouse", 5, 500),
        ],
    )
    connection.commit()
    connection.close()
    return database


def test_select_returns_rows(test_database):
    result = execute_readonly_sql(
        "SELECT product, quantity FROM sales ORDER BY product",
        test_database,
    )
    assert result["status"] == "success"
    assert result["row_count"] == 2
    assert result["rows"][0]["product"] == "Laptop"
    assert result["read_columns"] == ["product", "quantity"]


def test_aggregate_query(test_database):
    result = execute_readonly_sql(
        "SELECT SUM(quantity * price) AS revenue FROM sales",
        test_database,
    )
    assert result["rows"][0]["revenue"] == 102500


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM sales",
        "DROP TABLE sales",
        "UPDATE sales SET quantity = 0",
        "INSERT INTO sales VALUES ('Phone', 1, 100)",
    ],
)
def test_rejects_write_queries(test_database, query):
    with pytest.raises(SQLServiceError):
        execute_readonly_sql(query, test_database)


def test_rejects_multiple_statements(test_database):
    with pytest.raises(SQLServiceError):
        execute_readonly_sql(
            "SELECT * FROM sales; DROP TABLE sales;",
            test_database,
        )


def test_rejects_empty_query(test_database):
    with pytest.raises(ValueError):
        execute_readonly_sql("", test_database)


def test_missing_database_fails():
    with pytest.raises(SQLServiceError, match="does not exist"):
        execute_readonly_sql("SELECT 1", "missing_database.db")


def test_rejects_multiple_read_statements(test_database):
    with pytest.raises(SQLServiceError):
        execute_readonly_sql("SELECT 1; SELECT 2", test_database)


def test_rejects_unapproved_sql_functions(test_database):
    with pytest.raises(SQLServiceError):
        execute_readonly_sql("SELECT randomblob(100)", test_database)


def test_rejects_result_with_too_many_rows(test_database):
    with sqlite3.connect(test_database) as connection:
        connection.executemany(
            "INSERT INTO sales VALUES (?, ?, ?)",
            [(f"item-{index}", 1, 1) for index in range(501)],
        )

    with pytest.raises(SQLServiceError, match="too many rows"):
        execute_readonly_sql("SELECT * FROM sales", test_database)


def test_rejects_result_that_exceeds_size_limit(test_database):
    with sqlite3.connect(test_database) as connection:
        connection.execute(
            "INSERT INTO sales VALUES (?, ?, ?)",
            ("x" * 1_000_001, 1, 1),
        )

    with pytest.raises(SQLServiceError, match="maximum allowed size"):
        execute_readonly_sql(
            "SELECT product FROM sales WHERE quantity = 1 AND price = 1",
            test_database,
        )


def test_dataset_scope_cannot_read_registry_or_other_tables(tmp_path):
    database = tmp_path / "scoped.db"
    dataset_id = "a" * 32
    allowed_table = f"dataset_{dataset_id}"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE datasets (dataset_id TEXT, table_name TEXT)"
        )
        connection.execute(
            f'CREATE TABLE "{allowed_table}" (value INTEGER)'
        )
        connection.execute("CREATE TABLE other_dataset (value INTEGER)")
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            (dataset_id, allowed_table),
        )
        connection.execute(f'INSERT INTO "{allowed_table}" VALUES (42)')
        connection.execute("INSERT INTO other_dataset VALUES (99)")

    result = execute_readonly_sql(
        f'SELECT value FROM "{allowed_table}"',
        database,
        allowed_table=allowed_table,
    )
    assert result["rows"] == [{"value": 42}]

    for query in (
        "SELECT dataset_id FROM datasets",
        "SELECT value FROM other_dataset",
        "SELECT name FROM sqlite_master",
    ):
        with pytest.raises(SQLServiceError):
            execute_readonly_sql(query, database, allowed_table=allowed_table)


def test_dataset_scope_supports_read_only_ctes(tmp_path):
    database = tmp_path / "scoped.db"
    dataset_id = "b" * 32
    allowed_table = f"dataset_{dataset_id}"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE datasets (dataset_id TEXT, table_name TEXT)"
        )
        connection.execute(
            f'CREATE TABLE "{allowed_table}" (value INTEGER)'
        )
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            (dataset_id, allowed_table),
        )
        connection.execute(f'INSERT INTO "{allowed_table}" VALUES (42)')

    result = execute_readonly_sql(
        f'WITH selected AS (SELECT value FROM "{allowed_table}") '
        "SELECT SUM(value) AS total FROM selected",
        database,
        allowed_table=allowed_table,
    )
    assert result["rows"] == [{"total": 42}]


def test_dataset_scope_allows_only_selected_tables_in_multi_sheet_query(tmp_path):
    database = tmp_path / "multi-sheet.db"
    selected_ids = ["c" * 32, "d" * 32]
    selected_tables = [f"dataset_{dataset_id}" for dataset_id in selected_ids]
    unselected_table = f"dataset_{'e' * 32}"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE datasets (dataset_id TEXT, table_name TEXT)"
        )
        for dataset_id, table_name, value in zip(
            selected_ids,
            selected_tables,
            (10, 20),
        ):
            connection.execute(
                f'CREATE TABLE "{table_name}" (amount INTEGER)'
            )
            connection.execute(
                f'INSERT INTO "{table_name}" VALUES (?)',
                (value,),
            )
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?)",
                (dataset_id, table_name),
            )
        connection.execute(
            f'CREATE TABLE "{unselected_table}" (amount INTEGER)'
        )
        connection.execute(
            "INSERT INTO datasets VALUES (?, ?)",
            ("e" * 32, unselected_table),
        )

    query = (
        f'SELECT SUM(amount) AS total FROM "{selected_tables[0]}" '
        f'UNION ALL SELECT SUM(amount) FROM "{selected_tables[1]}"'
    )
    result = execute_readonly_sql(
        query,
        database,
        allowed_tables=selected_tables,
    )
    assert result["rows"] == [{"total": 10}, {"total": 20}]
    assert {item["table"] for item in result["read_column_refs"]} == set(
        selected_tables
    )

    with pytest.raises(SQLServiceError):
        execute_readonly_sql(
            f'SELECT amount FROM "{unselected_table}"',
            database,
            allowed_tables=selected_tables,
        )
