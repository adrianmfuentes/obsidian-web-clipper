import sqlite3

import pytest

import database


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue.db")
    database.init_db()


def test_connection_is_closed_after_use():
    """
    Regression test: get_connection() must actually close the connection
    on exit, not just commit/rollback the transaction. A raw sqlite3.Connection
    used as `with conn:` only handles the transaction and leaks the handle.
    """
    with database.get_connection() as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connection_closed_even_on_error():
    with pytest.raises(ValueError):
        with database.get_connection() as conn:
            raise ValueError("boom")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_notes_roundtrip():
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (title, url, markdown, filename, content_hash) VALUES (?, ?, ?, ?, ?)",
            ("Title", "https://example.com", "# md", "file.md", "hash-1"),
        )
        conn.commit()

    with database.get_connection() as conn:
        rows = conn.execute("SELECT * FROM notes").fetchall()

    assert len(rows) == 1
    assert rows[0]["title"] == "Title"


def test_content_hash_is_unique():
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (title, url, markdown, filename, content_hash) VALUES (?, ?, ?, ?, ?)",
            ("Title", "https://example.com", "# md", "file.md", "dupe-hash"),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO notes (title, url, markdown, filename, content_hash) VALUES (?, ?, ?, ?, ?)",
                ("Other Title", "https://example.com/2", "# md2", "file2.md", "dupe-hash"),
            )
