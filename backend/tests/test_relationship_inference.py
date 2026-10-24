
import sqlite3

import pytest

from backend.app.services.relationship_inference import (
    RelationshipInferenceError,
    infer_relationships,
)


def create_test_database(database_path, include_match=True):
    """Create two small datasets for relationship testing."""

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                table_name TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE dataset_students (
                student_id INTEGER,
                student_name TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE dataset_marks (
                student_id INTEGER,
                marks INTEGER
            )
            """
        )

        connection.executemany(
            "INSERT INTO dataset_students VALUES (?, ?)",
            [
                (101, "Amit"),
                (102, "Neha"),
                (103, "Ravi"),
            ],
        )

        if include_match:
            connection.executemany(
                "INSERT INTO dataset_marks VALUES (?, ?)",
                [
                    (101, 85),
                    (102, 92),
                    (103, 78),
                ],
            )
        else:
            connection.executemany(
                "INSERT INTO dataset_marks VALUES (?, ?)",
                [
                    (201, 85),
                    (202, 92),
                    (203, 78),
                ],
            )

        connection.executemany(
            """
            INSERT INTO datasets (dataset_id, filename, table_name)
            VALUES (?, ?, ?)
            """,
            [
                ("students-1", "students.csv", "dataset_students"),
                ("marks-1", "marks.csv", "dataset_marks"),
            ],
        )


def test_detects_relationship_with_shared_student_ids(tmp_path):
    database_path = tmp_path / "test.db"
    create_test_database(database_path)

    result = infer_relationships(database_path)

    assert result["dataset_count"] == 2
    assert result["relationship_count"] == 1

    relationship = result["relationships"][0]

    assert relationship["shared_value_count"] == 3
    assert relationship["confidence"] > 0
    assert relationship["status"] == "candidate"

    columns = {
        relationship["left"]["column"],
        relationship["right"]["column"],
    }
    assert columns == {"student_id", "student_id"}


def test_does_not_match_disjoint_student_ids(tmp_path):
    database_path = tmp_path / "test.db"
    create_test_database(database_path, include_match=False)

    result = infer_relationships(database_path)

    assert result["relationship_count"] == 0
    assert result["relationships"] == []


def test_detects_legitimate_customer_order_relationship(tmp_path):
    database_path = tmp_path / "customer_orders.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                table_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_customers (
                customer_id INTEGER,
                customer_name TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_orders (
                order_id INTEGER,
                customer_id INTEGER,
                amount REAL
            )
            """
        )

        connection.executemany(
            "INSERT INTO dataset_customers VALUES (?, ?)",
            [
                (101, "Alice"),
                (102, "Bob"),
                (103, "Cara"),
                (104, "Dina"),
            ],
        )
        connection.executemany(
            "INSERT INTO dataset_orders VALUES (?, ?, ?)",
            [
                (1, 101, 42.50),
                (2, 102, 31.00),
                (3, 103, 25.25),
                (4, 104, 77.00),
                (5, 101, 18.00),
            ],
        )
        connection.executemany(
            """
            INSERT INTO datasets (dataset_id, original_filename, table_name)
            VALUES (?, ?, ?)
            """,
            [
                ("customers", "customers.csv", "dataset_customers"),
                ("orders", "orders.csv", "dataset_orders"),
            ],
        )

    result = infer_relationships(database_path)

    assert result["dataset_count"] == 2
    assert result["relationship_count"] == 1

    relationship = result["relationships"][0]
    assert relationship["left"]["dataset_id"] == "customers"
    assert relationship["left"]["column"] == "customer_id"
    assert relationship["right"]["dataset_id"] == "orders"
    assert relationship["right"]["column"] == "customer_id"
    assert relationship["shared_value_count"] == 4
    assert relationship["overlap_ratio"] == 1.0
    assert relationship["confidence"] == 1.0
    assert relationship["status"] == "candidate"
    assert relationship["evidence"]


def test_ignores_unrelated_columns_with_coincidental_overlap(tmp_path):
    database_path = tmp_path / "coincidental_overlap.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                table_name TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_status (
                status_code INTEGER,
                status_name TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_flags (
                flag INTEGER,
                category TEXT
            )
            """
        )

        connection.executemany(
            "INSERT INTO dataset_status VALUES (?, ?)",
            [
                (1, "new"),
                (2, "pending"),
                (3, "closed"),
                (4, "archived"),
            ],
        )
        connection.executemany(
            "INSERT INTO dataset_flags VALUES (?, ?)",
            [
                (1, "alpha"),
                (2, "beta"),
                (3, "gamma"),
                (4, "delta"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO datasets (dataset_id, original_filename, table_name)
            VALUES (?, ?, ?)
            """,
            [
                ("status", "status.csv", "dataset_status"),
                ("flags", "flags.csv", "dataset_flags"),
            ],
        )

    result = infer_relationships(database_path)

    assert result["relationship_count"] == 0
    assert result["relationships"] == []


def test_missing_database_raises_clear_error(tmp_path):
    database_path = tmp_path / "missing.db"

    with pytest.raises(
        RelationshipInferenceError,
        match="Database not found",
    ):
        infer_relationships(database_path)




def test_does_not_infer_relationship_between_identical_dataset_copies(
    tmp_path,
):
    """Identical dataset copies should not create redundant relationships."""
    database_path = tmp_path / "duplicate_datasets.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                table_name TEXT NOT NULL
            )
            """
        )

        # Create two separate physical tables with identical contents.
        connection.execute(
            """
            CREATE TABLE dataset_students_a (
                student_id INTEGER,
                student_name TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_students_b (
                student_id INTEGER,
                student_name TEXT
            )
            """
        )

        student_rows = [
            (101, "Amit"),
            (102, "Neha"),
            (103, "Ravi"),
        ]

        connection.executemany(
            "INSERT INTO dataset_students_a VALUES (?, ?)",
            student_rows,
        )
        connection.executemany(
            "INSERT INTO dataset_students_b VALUES (?, ?)",
            student_rows,
        )

        connection.executemany(
            """
            INSERT INTO datasets (dataset_id, filename, table_name)
            VALUES (?, ?, ?)
            """,
            [
                (
                    "students-copy-1",
                    "students.csv",
                    "dataset_students_a",
                ),
                (
                    "students-copy-2",
                    "students.csv",
                    "dataset_students_b",
                ),
            ],
        )

    result = infer_relationships(database_path)

    assert result["dataset_count"] == 2
    assert result["relationship_count"] == 0
    assert result["relationships"] == []



def test_detects_key_name_variation_with_shared_values(tmp_path):
    """Recognize reordered key names when their values overlap."""
    database_path = tmp_path / "key_name_variation.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE datasets (
                dataset_id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                table_name TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE dataset_students (
                student_id INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE dataset_exam_results (
                id_student INTEGER
            )
            """
        )

        connection.executemany(
            "INSERT INTO dataset_students VALUES (?)",
            [(101,), (102,), (103,)],
        )
        connection.executemany(
            "INSERT INTO dataset_exam_results VALUES (?)",
            [(101,), (102,), (103,)],
        )

        connection.executemany(
            """
            INSERT INTO datasets
                (dataset_id, original_filename, table_name)
            VALUES (?, ?, ?)
            """,
            [
                ("students", "students.csv", "dataset_students"),
                ("exam-results", "exam_results.csv", "dataset_exam_results"),
            ],
        )

    result = infer_relationships(database_path)

    assert result["relationship_count"] == 1

    relationship = result["relationships"][0]
    assert relationship["shared_value_count"] == 3
    assert relationship["status"] == "candidate"
