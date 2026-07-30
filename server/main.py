"""
main.py – Obsidian Web Clipper Queue Server
FastAPI microservice running on Oracle ARM64.

Endpoints:
  POST /capture      – Receive article, call Gemini, store Markdown in queue.
  GET  /pull-notes   – Return all queued notes as JSON and delete them (pop).
  GET  /health       – Simple liveness check (no auth required).
"""

import os
import re
import secrets
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import get_connection, init_db

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


@app.post("/capture", status_code=202, dependencies=[Depends(verify_token)])
async def capture(req: CaptureRequest):
    """
    Receive an article from the Chrome extension.
    Call Gemini to process it, then store the result in the SQLite queue.
    Returns 202 Accepted immediately once stored.
    """
    log.info("Capture request for: %s", req.url)

    if not req.text or len(req.text.strip()) < 50:
        raise HTTPException(status_code=422, detail="Article text too short to process.")

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

    # Store in SQLite queue
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO notes (title, url, markdown, filename) VALUES (?, ?, ?, ?)",
            (req.title, req.url, markdown, filename),
        )
        conn.commit()

    log.info("Queued note: %s", filename)
    return {"status": "queued", "filename": filename}


@app.get("/pull-notes", dependencies=[Depends(verify_token)])
def pull_notes():
    """
    Return all pending notes as a JSON array, then delete them from the queue.
    This is the 'pop' operation – call it from the local Obsidian client.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, url, markdown, created_at, filename FROM notes ORDER BY id ASC"
        ).fetchall()

        if not rows:
            return {"count": 0, "notes": []}

        notes = [dict(row) for row in rows]
        ids   = [row["id"] for row in rows]

        # Delete the fetched notes atomically
        conn.execute(f"DELETE FROM notes WHERE id IN ({','.join('?' * len(ids))})", ids)
        conn.commit()

    log.info("Pulled and deleted %d note(s) from queue.", len(notes))
    return {"count": len(notes), "notes": notes}
