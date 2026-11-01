"""Database configuration for GraphSheet AI."""

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "graphsheet.db"
load_dotenv(PROJECT_ROOT / ".env")


def get_database_url() -> str:
    """Return the configured database URL, defaulting to local SQLite."""
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url
    return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
