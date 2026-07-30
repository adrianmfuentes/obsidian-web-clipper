"""
database.py
Sets up and provides access to the SQLite queue database.

The 'notes' table stores processed Markdown notes waiting to be
pulled down by the local Obsidian client.
"""

import contextlib
import sqlite3
import os
from pathlib import Path
from typing import Iterator

# Data directory – bind-mounted as a Docker volume so notes survive restarts.
# Not created here at import time: merely `import database` shouldn't touch
# the filesystem (and the default /app/data is only writable inside the
# Docker image, where the Dockerfile already creates it). init_db() below
# creates DB_PATH's parent lazily, so tests that monkeypatch DB_PATH to a
# tmp_path never touch the real default.
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DB_PATH = DATA_DIR / "queue.db"


@contextlib.contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Yield a thread-safe SQLite connection with row_factory set.

    A plain sqlite3.Connection used as `with conn:` only wraps the
    transaction (commit/rollback) — it does NOT close the connection.
    This contextmanager guarantees the connection is actually closed,
    even on error, so callers can keep writing `with get_connection() as conn:`.
    """
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows accessible as dicts
    conn.execute("PRAGMA journal_mode=WAL") # safe for concurrent reads
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Ensure the data directory exists, then create the notes table if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT    NOT NULL,
                url           TEXT    NOT NULL,
                markdown      TEXT    NOT NULL,   -- Gemini output
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                filename      TEXT    NOT NULL,   -- suggested .md filename
                content_hash  TEXT    NOT NULL UNIQUE  -- dedupes repeat /capture calls
            )
        """)
        conn.commit()
