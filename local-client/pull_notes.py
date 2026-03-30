#!/usr/bin/env python3
"""
pull_notes.py – Obsidian Web Clipper Local Client
==================================================
Connects to the Oracle server queue, downloads all pending notes,
and writes them as .md files into your Obsidian Inbox folder.

SETUP:
  1. pip install requests          (or: python -m pip install requests)
  2. Edit the CONFIG section below (or use environment variables).
  3. Schedule this script:
       Windows Task Scheduler : python pull_notes.py  (trigger: at logon + every 30 min)
       Linux/macOS cron       : */30 * * * * /usr/bin/python3 /path/to/pull_notes.py

ENVIRONMENT VARIABLE OVERRIDES (optional – useful for CI / dotfiles):
  CLIPPER_SERVER_URL   e.g. https://your-server.com:8000
  CLIPPER_AUTH_TOKEN   your secret token
  CLIPPER_INBOX_PATH   full path to your Obsidian Inbox folder
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library not found. Run: pip install requests")
    sys.exit(1)

# ─── Configuration ─────────────────────────────────────────────────────────────
# Edit these values OR set the matching environment variables.

CONFIG = {
    # Base URL of your Oracle FastAPI server (no trailing slash)
    "server_url": os.getenv("CLIPPER_SERVER_URL", "https://YOUR_ORACLE_SERVER:8000"),

    # Must match AUTH_TOKEN in docker-compose.yml
    "auth_token": os.getenv("CLIPPER_AUTH_TOKEN", "CHANGE_ME_TO_A_LONG_RANDOM_STRING"),

    # Absolute path to your Obsidian Inbox folder
    # Windows example: C:/Users/YourName/Documents/MyVault/Inbox
    # macOS  example:  /Users/YourName/Documents/MyVault/Inbox
    "inbox_path": os.getenv(
        "CLIPPER_INBOX_PATH",
        r"C:\Users\YOUR_USERNAME\Documents\ObsidianVault\Inbox"
    ),

    # How long to wait for the server before giving up (seconds)
    "request_timeout": int(os.getenv("CLIPPER_TIMEOUT", "30")),
}

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("obsidian-clipper")


# ─── Helpers ───────────────────────────────────────────────────────────────────

def sanitize_filename(filename: str) -> str:
    """
    Ensure the filename is safe for all platforms.
    The server already generates a good name, but we double-check here.
    """
    # Replace characters illegal on Windows / macOS
    for char in r'\/*?:"<>|':
        filename = filename.replace(char, "-")
    return filename.strip()


def write_note(inbox: Path, filename: str, markdown: str) -> Path:
    """Write a single Markdown note to the Inbox folder."""
    safe_name = sanitize_filename(filename)
    if not safe_name.endswith(".md"):
        safe_name += ".md"

    target = inbox / safe_name

    # Avoid overwriting an existing file (add a suffix if needed)
    counter = 1
    while target.exists():
        stem = Path(safe_name).stem
        target = inbox / f"{stem}_{counter}.md"
        counter += 1

    target.write_text(markdown, encoding="utf-8")
    return target


def pull_and_save() -> int:
    """
    Fetch all queued notes from the server and write them to the Inbox.
    Returns the number of notes saved.
    """
    server_url = CONFIG["server_url"].rstrip("/")
    token      = CONFIG["auth_token"]
    inbox      = Path(CONFIG["inbox_path"])

    # ── Validate config ──────────────────────────────────────────────────────
    if "YOUR_ORACLE_SERVER" in server_url or "CHANGE_ME" in server_url:
        log.error("Please edit CONFIG in pull_notes.py before running.")
        return 0

    if "YOUR_USERNAME" in str(inbox) or not inbox.exists():
        log.warning(
            "Inbox path does not exist: %s\n"
            "Creating it now – please verify the path is correct.",
            inbox,
        )
        inbox.mkdir(parents=True, exist_ok=True)

    # ── Request notes from server ────────────────────────────────────────────
    endpoint = f"{server_url}/pull-notes"
    log.info("Connecting to %s …", endpoint)

    try:
        response = requests.get(
            endpoint,
            headers={"X-Auth-Token": token},
            timeout=CONFIG["request_timeout"],
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        log.error("Could not connect to %s – is the server running?", server_url)
        return 0
    except requests.exceptions.Timeout:
        log.error("Request timed out after %ss.", CONFIG["request_timeout"])
        return 0
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 401:
            log.error("Authentication failed – check AUTH_TOKEN.")
        else:
            log.error("Server error %s: %s", exc.response.status_code, exc.response.text)
        return 0

    # ── Parse response ───────────────────────────────────────────────────────
    data = response.json()
    count = data.get("count", 0)

    if count == 0:
        log.info("No pending notes in queue.")
        return 0

    log.info("Received %d note(s). Writing to: %s", count, inbox)

    saved = 0
    for note in data.get("notes", []):
        try:
            filename = note.get("filename") or f"note-{note['id']}.md"
            path = write_note(inbox, filename, note["markdown"])
            log.info("  ✓ Saved: %s", path.name)
            saved += 1
        except Exception as exc:
            log.error("  ✗ Failed to write note '%s': %s", note.get("filename"), exc)

    return saved


# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("─── Obsidian Web Clipper – Pull Run (%s) ───",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    saved = pull_and_save()

    if saved > 0:
        log.info("Done. %d new note(s) added to your Obsidian Inbox.", saved)
    else:
        log.info("Done. Nothing new.")
