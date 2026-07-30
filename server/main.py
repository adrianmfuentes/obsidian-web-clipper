"""
main.py – Obsidian Web Clipper Queue Server
FastAPI microservice running on Oracle ARM64.

Endpoints:
  POST /capture      – Receive article, call Gemini, store Markdown in queue.
                        Repeat calls with the same URL+text are deduped (no
                        extra Gemini spend, no duplicate queue rows).
  GET  /pull-notes   – Return pending notes as JSON. Does NOT delete them —
                        call POST /ack-notes once they're safely written to
                        disk. This makes the pull a safe-to-retry read.
  POST /ack-notes    – Delete previously-pulled notes by id.
  GET  /wait-notes   – Long-poll: blocks until a new note is queued (or the
                        timeout elapses), then returns pending notes. Lets
                        the local client react in seconds instead of on a
                        fixed polling interval.
  GET  /health       – Simple liveness check (no auth required).
"""

import asyncio
import hashlib
import os
import re
import secrets
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import get_connection, init_db

# Long-poll wait is clamped to this range so a slow/careless client can't tie
# up a worker indefinitely, and so it stays under typical reverse-proxy
# read-timeout defaults (e.g. the 60s Caddy example in the README).
MIN_WAIT_TIMEOUT = 1.0
MAX_WAIT_TIMEOUT = 55.0

# ─── Configuration (set via environment variables in docker-compose) ───────────
AUTH_TOKEN   = os.environ["AUTH_TOKEN"]          # Required – no default
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]    # Required – no default
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL   = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# ─── App setup ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# Signaled every time /capture queues a new note, so /wait-notes can wake up
# waiting clients instead of making them poll on a fixed interval. Created in
# the lifespan (not at import time) so it binds to the loop that's actually
# serving requests.
new_note_condition: asyncio.Condition | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global new_note_condition
    init_db()
    new_note_condition = asyncio.Condition()
    log.info("Database ready at %s", os.getenv("DATA_DIR", "/app/data"))
    yield


app = FastAPI(
    title="Obsidian Web Clipper Queue",
    description="Captures web articles, processes them with Gemini, queues for Obsidian.",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow the Chrome extension to reach this server (adjust origin in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Chrome extensions don't have a stable origin
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── Auth dependency ───────────────────────────────────────────────────────────
def verify_token(x_auth_token: Annotated[str | None, Header()] = None):
    """Dependency – raises 401 if the token header is missing or wrong."""
    if x_auth_token is None or not secrets.compare_digest(x_auth_token, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Auth-Token")


# ─── Schemas ───────────────────────────────────────────────────────────────────
class CaptureRequest(BaseModel):
    title: str
    url: str       # plain str – Chrome sends the URL directly
    text: str


class NoteResponse(BaseModel):
    id: int
    title: str
    url: str
    markdown: str
    created_at: str
    filename: str


class AckRequest(BaseModel):
    ids: list[int]


# ─── Helpers ───────────────────────────────────────────────────────────────────
def build_gemini_prompt(title: str, url: str, text: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""You are an expert knowledge manager helping someone build their Obsidian second-brain.

Analyze the article below and return a single, well-structured Obsidian Markdown note.

REQUIREMENTS:
1. Start with YAML frontmatter (fenced with ---) containing:
   - title: (the article title, quoted)
   - url: (the original URL)
   - date: {today}
   - tags: (3–5 relevant lowercase tags as a YAML list)
   - source: web-clipper
2. After the frontmatter, provide these sections:
   ## Summary
   A clear, concise 3–5 sentence summary of the article.

   ## Key Ideas
   Exactly 5 numbered key ideas, each with a 1–2 sentence explanation.

   ## Highlights
   2–3 direct quotes or specific data points worth preserving verbatim.

   ## My Notes
   Leave this section empty (just the heading) — the user fills it in later.

OUTPUT ONLY the Markdown note. No preamble, no explanations outside the note.

---
Article Title: {title}
Article URL: {url}

Article Content:
{text}
"""


def compute_content_hash(url: str, text: str) -> str:
    """
    Fingerprint a capture by URL + article text so repeat /capture calls for
    the same article (double-clicks, retried requests) are recognized as
    duplicates before we spend a Gemini call on them.
    """
    normalized = f"{url.strip()}\n{text.strip()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def sanitize_filename(title: str) -> str:
    """Convert an article title into a safe .md filename."""
    clean = re.sub(r'[\\/*?:"<>|]', '', title)   # remove illegal chars
    clean = re.sub(r'\s+', '-', clean.strip())     # spaces → dashes
    clean = clean[:80]                             # max 80 chars
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{clean}.md"


async def call_gemini(prompt: str) -> str:
    """Send a prompt to Gemini and return the text response."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 2048,
        },
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        log.error("Unexpected Gemini response structure: %s", data)
        raise ValueError("Could not parse Gemini response") from exc


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness probe – no auth required."""
    return {"status": "ok", "model": GEMINI_MODEL}


def fetch_pending_notes() -> dict:
    """Read-only snapshot of everything currently queued, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, markdown, created_at, filename FROM notes ORDER BY id ASC"
        ).fetchall()
    notes = [dict(row) for row in rows]
    return {"count": len(notes), "notes": notes}


@app.post("/capture", status_code=202, dependencies=[Depends(verify_token)])
async def capture(req: CaptureRequest):
    """
    Receive an article from the Chrome extension.
    Call Gemini to process it, then store the result in the SQLite queue.
    Returns 202 Accepted immediately once stored.

    Repeat calls carrying the same URL+text (double-clicks, retried requests,
    the extension re-sending after a flaky response) are recognized via
    content_hash and short-circuited before touching Gemini.
    """
    log.info("Capture request for: %s", req.url)

    if not req.text or len(req.text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Article text too short to process.")

    content_hash = compute_content_hash(req.url, req.text)

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT filename FROM notes WHERE content_hash = ?", (content_hash,)
        ).fetchone()

    if existing is not None:
        log.info("Duplicate capture ignored for: %s", req.url)
        return {"status": "duplicate", "filename": existing["filename"]}

    # Call Gemini
    try:
        prompt   = build_gemini_prompt(req.title, req.url, req.text)
        markdown = await call_gemini(prompt)
    except httpx.HTTPStatusError as exc:
        log.error("Gemini API error: %s", exc.response.text)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc.response.status_code}")
    except Exception as exc:
        log.error("Gemini call failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    filename = sanitize_filename(req.title)

    # Store in SQLite queue. content_hash is UNIQUE, so a concurrent duplicate
    # request that raced past the check above fails here instead of double-queuing.
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO notes (title, url, markdown, filename, content_hash) VALUES (?, ?, ?, ?, ?)",
                (req.title, req.url, markdown, filename, content_hash),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        log.info("Duplicate capture raced past the pre-check for: %s", req.url)
        return {"status": "duplicate", "filename": filename}

    log.info("Queued note: %s", filename)

    # Wake up any client long-polling on /wait-notes.
    async with new_note_condition:
        new_note_condition.notify_all()

    return {"status": "queued", "filename": filename}


@app.get("/pull-notes", dependencies=[Depends(verify_token)])
def pull_notes():
    """
    Return all pending notes as a JSON array. This is a non-destructive read —
    notes stay in the queue until the caller confirms receipt via POST
    /ack-notes, so a client crash between fetching and writing to disk can't
    lose notes.
    """
    result = fetch_pending_notes()
    log.info("Pulled %d note(s) from queue (not yet acked).", result["count"])
    return result


@app.post("/ack-notes", dependencies=[Depends(verify_token)])
def ack_notes(req: AckRequest):
    """
    Delete previously-pulled notes by id, once the caller has safely
    persisted them (e.g. written to the Obsidian Inbox folder).
    """
    if not req.ids:
        return {"deleted": 0}

    with get_connection() as conn:
        placeholders = ",".join("?" * len(req.ids))
        cur = conn.execute(f"DELETE FROM notes WHERE id IN ({placeholders})", req.ids)
        conn.commit()
        deleted = cur.rowcount

    log.info("Acked and deleted %d note(s) from queue.", deleted)
    return {"deleted": deleted}


@app.get("/wait-notes", dependencies=[Depends(verify_token)])
async def wait_notes(timeout: float = 25.0):
    """
    Long-poll: return immediately if a note is already pending, otherwise
    block until a new note is queued or `timeout` seconds elapse, then return
    whatever is currently pending (same shape as /pull-notes). Lets the local
    client react within seconds of a capture instead of waiting for its next
    fixed polling interval.
    """
    already_pending = fetch_pending_notes()
    if already_pending["count"] > 0:
        return already_pending

    clamped_timeout = max(MIN_WAIT_TIMEOUT, min(timeout, MAX_WAIT_TIMEOUT))

    async with new_note_condition:
        try:
            await asyncio.wait_for(new_note_condition.wait(), timeout=clamped_timeout)
        except asyncio.TimeoutError:
            pass

    return fetch_pending_notes()
