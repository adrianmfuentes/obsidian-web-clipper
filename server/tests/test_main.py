import pytest
from fastapi import HTTPException

import main


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
