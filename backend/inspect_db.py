import sqlite3
from pathlib import Path

db_path = Path("data/graphsheet.db")

if not db_path.exists():
    print(f"Database not found: {db_path.resolve()}")
else:
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' ORDER BY name"
        ).fetchall()

        for name, schema in tables:
            print(f"\nTABLE: {name}")
            print(schema or "Schema unavailable")

            columns = conn.execute(
                f'PRAGMA table_info("{name}")'
            ).fetchall()

            print("COLUMNS:", [column[1] for column in columns])
