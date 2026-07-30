import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import database
import main

AUTH_HEADERS = {"X-Auth-Token": main.AUTH_TOKEN}


@pytest.fixture(scope="module")
def client():
    # Runs the lifespan (and binds new_note_condition) once, on a single
    # persistent loop shared by every request in this module.
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue.db")
    database.init_db()


def test_sanitize_filename_strips_illegal_chars():
    name = main.sanitize_filename('Title: "with" <illegal>/chars\\here?*')
    stem = name.split("-", 2)[-1]  # drop the leading timestamp
    assert not any(c in stem for c in '\\/*?:"<>|')
    assert name.endswith(".md")


def test_sanitize_filename_truncates_long_titles():
    name = main.sanitize_filename("x" * 500)
    # timestamp (15 chars: YYYYMMDD-HHMMSS) + "-" + up to 80 chars + ".md"
    assert len(name) <= 15 + 1 + 80 + len(".md")


def test_build_gemini_prompt_includes_inputs():
    prompt = main.build_gemini_prompt("My Title", "https://example.com", "Some body text")
    assert "My Title" in prompt
    assert "https://example.com" in prompt
    assert "Some body text" in prompt


def test_verify_token_rejects_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        main.verify_token(None)
    assert exc_info.value.status_code == 401


def test_verify_token_rejects_wrong_token():
    with pytest.raises(HTTPException) as exc_info:
        main.verify_token("not-the-right-token")
    assert exc_info.value.status_code == 401


def test_verify_token_accepts_correct_token():
    main.verify_token(main.AUTH_TOKEN)  # should not raise


def test_capture_dedupes_repeat_requests(client):
    payload = {"title": "T", "url": "https://example.com/a", "text": "x" * 60}
    with patch("main.call_gemini", new=AsyncMock(return_value="# note")) as mock_gemini:
        first = client.post("/capture", json=payload, headers=AUTH_HEADERS)
        second = client.post("/capture", json=payload, headers=AUTH_HEADERS)

    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert second.status_code == 202
    assert second.json()["status"] == "duplicate"
    mock_gemini.assert_called_once()

    pending = client.get("/pull-notes", headers=AUTH_HEADERS).json()
    assert pending["count"] == 1


def test_pull_notes_is_non_destructive_until_acked(client):
    payload = {"title": "T2", "url": "https://example.com/b", "text": "y" * 60}
    with patch("main.call_gemini", new=AsyncMock(return_value="# note2")):
        client.post("/capture", json=payload, headers=AUTH_HEADERS)

    first_pull = client.get("/pull-notes", headers=AUTH_HEADERS).json()
    second_pull = client.get("/pull-notes", headers=AUTH_HEADERS).json()
    assert first_pull["count"] == 1
    assert second_pull["count"] == 1  # still queued — pull doesn't delete

    note_id = second_pull["notes"][0]["id"]
    ack = client.post("/ack-notes", json={"ids": [note_id]}, headers=AUTH_HEADERS)
    assert ack.json() == {"deleted": 1}

    after_ack = client.get("/pull-notes", headers=AUTH_HEADERS).json()
    assert after_ack["count"] == 0


def test_ack_notes_with_no_ids_is_a_noop(client):
    result = client.post("/ack-notes", json={"ids": []}, headers=AUTH_HEADERS)
    assert result.json() == {"deleted": 0}


def test_wait_notes_returns_already_pending_note_immediately(client):
    """
    Regression test: a note queued before /wait-notes was ever called must
    come back right away. The long-poll condition is only notified at the
    moment /capture commits, so a naive "always wait first" implementation
    would block for the full timeout even though data is already sitting
    in the queue — this asserts it doesn't.
    """
    payload = {"title": "T3", "url": "https://example.com/c", "text": "z" * 60}
    with patch("main.call_gemini", new=AsyncMock(return_value="# note3")):
        client.post("/capture", json=payload, headers=AUTH_HEADERS)

    start = time.monotonic()
    result = client.get("/wait-notes", params={"timeout": 10}, headers=AUTH_HEADERS)
    elapsed = time.monotonic() - start

    assert result.json()["count"] == 1
    assert elapsed < 2, f"expected an immediate return, took {elapsed:.2f}s"


def test_wait_notes_times_out_with_empty_queue(client):
    result = client.get("/wait-notes", params={"timeout": 1}, headers=AUTH_HEADERS)
    assert result.json() == {"count": 0, "notes": []}
