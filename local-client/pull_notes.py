#!/usr/bin/env python3
"""
pull_notes.py – Obsidian Web Clipper Local Client
==================================================
Connects to the Oracle server queue, downloads pending notes, writes them as
.md files into your Obsidian Inbox folder, then acks them so the server can
drop them from the queue. A note is only acked after it's been written to
disk, so a crash mid-run just means it's fetched again next time — nothing
is lost.

Two modes:
  One-shot (default) : fetch whatever's pending once, then exit.
                        Good for Task Scheduler / cron on a fixed interval.
  Watch (--watch)     : long-poll the server so new notes land in your vault
                        within seconds of being clipped, instead of waiting
                        for the next scheduled run.

SETUP:
  1. pip install requests          (or: python -m pip install requests)
  2. Edit the CONFIG section below (or use environment variables).
  3. Schedule this script (one-shot mode):
       Windows Task Scheduler : pythonw pull_notes.py  (trigger: at logon + every 30 min)
       Linux/macOS cron       : */30 * * * * /usr/bin/python3 /path/to/pull_notes.py
     ...or run it once, long-lived, in watch mode instead:
       pythonw pull_notes.py --watch

  On Windows, use `pythonw.exe` (not `python.exe`) when scheduling this —
  schedule_task.ps1 does this automatically. `python.exe` is a console app,
  so Task Scheduler briefly flashes a terminal window into the foreground on
  every run; `pythonw.exe` is the windowless twin of the same interpreter
  and runs fully in the background. Since that means there's no console to
  print to, this script always logs to pull_notes.log next to itself (or
  CLIPPER_LOG_PATH) in addition to stdout — check there if a scheduled run
  didn't do what you expected.

ENVIRONMENT VARIABLE OVERRIDES (optional – useful for CI / dotfiles):
  CLIPPER_SERVER_URL   e.g. https://your-server.com:8000
  CLIPPER_AUTH_TOKEN   your secret token
  CLIPPER_INBOX_PATH   full path to your Obsidian Inbox folder
  CLIPPER_LOG_PATH     full path to the log file (default: pull_notes.log next to this script)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import logging
import logging.handlers
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
    "server_url": "https://YOUR_ORACLE_SERVER",
    "auth_token":  "CHANGE_ME_TO_A_LONG_RANDOM_STRING",
    "inbox_path":  r"C:\Users\YOUR_USERNAME\Documents\ObsidianVault\Inbox",
    "request_timeout": 30,
    # How long the server may hold a --watch request open waiting for a new
    # note before replying empty. Must stay comfortably under any reverse
    # proxy's read-timeout (the README's Caddy example uses the default 60s).
    "wait_timeout": 25,
    # Where run history goes. Matters most when scheduled with pythonw.exe
    # (see schedule_task.ps1) — that runs with no console at all, so this
    # file is the only place output goes.
    "log_path": Path(__file__).resolve().parent / "pull_notes.log",
}

# ─── Logging ───────────────────────────────────────────────────────────────────
# Always logs to a rotating file (capped at ~1MB x 3) so scheduled runs leave
# a trail even when launched via pythonw.exe, which has no console to print
# to. Also logs to stdout/stderr for when you run the script manually.
log = logging.getLogger("obsidian-clipper")
log.setLevel(logging.INFO)
_log_formatter = logging.Formatter(
    fmt="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)
log.addHandler(_console_handler)

try:
    log_path = Path(os.environ.get("CLIPPER_LOG_PATH", CONFIG["log_path"]))
    _file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    _file_handler.setFormatter(_log_formatter)
    log.addHandler(_file_handler)
except OSError as exc:
    log.warning("Could not open log file %s (%s) — file logging disabled.", CONFIG["log_path"], exc)


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


def get_config():
    server_url = os.environ.get("CLIPPER_SERVER_URL", CONFIG["server_url"]).rstrip("/")
    token      = os.environ.get("CLIPPER_AUTH_TOKEN", CONFIG["auth_token"])
    inbox      = Path(os.environ.get("CLIPPER_INBOX_PATH", CONFIG["inbox_path"]))
    return server_url, token, inbox


def config_is_valid(server_url: str, inbox: Path) -> bool:
    if "YOUR_ORACLE_SERVER" in server_url or "CHANGE_ME" in server_url:
        log.error("Please edit CONFIG in pull_notes.py before running.")
        return False

    if "YOUR_USERNAME" in str(inbox) or not inbox.exists():
        log.warning(
            "Inbox path does not exist: %s\n"
            "Creating it now - please verify the path is correct.",
            inbox,
        )
        inbox.mkdir(parents=True, exist_ok=True)

    return True


def request_notes(server_url: str, token: str, path: str, timeout: float, params: dict | None = None):
    """
    GET a notes payload ({"count": int, "notes": [...]}) from the server.
    Returns None (already logged) on any connection/auth/server error.
    """
    endpoint = f"{server_url}{path}"
    try:
        response = requests.get(
            endpoint,
            headers={"X-Auth-Token": token},
            params=params,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        log.error("Could not connect to %s - is the server running?", server_url)
        return None
    except requests.exceptions.Timeout:
        log.error("Request to %s timed out after %ss.", endpoint, timeout)
        return None
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 401:
            log.error("Authentication failed - check AUTH_TOKEN.")
        else:
            log.error("Server error %s: %s", exc.response.status_code, exc.response.text)
        return None

    return response.json()


def ack_notes(server_url: str, token: str, ids: list, timeout: float) -> None:
    """Tell the server it's safe to drop these ids from the queue."""
    if not ids:
        return
    try:
        response = requests.post(
            f"{server_url}/ack-notes",
            headers={"X-Auth-Token": token},
            json={"ids": ids},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # Not fatal: the notes are already written to disk. Worst case they
        # get re-delivered next run and write_note()'s de-dupe suffix kicks in.
        log.warning("Failed to ack %d note(s) (will be re-delivered next run): %s", len(ids), exc)


def write_and_ack(data: dict, inbox: Path, server_url: str, token: str, timeout: float) -> int:
    """Write every note in `data` to the Inbox, then ack only the ones that saved cleanly."""
    count = data.get("count", 0)
    if count == 0:
        return 0

    log.info("Received %d note(s). Writing to: %s", count, inbox)

    saved_ids = []
    for note in data.get("notes", []):
        try:
            filename = note.get("filename") or f"note-{note['id']}.md"
            path = write_note(inbox, filename, note["markdown"])
            log.info("  [OK] Saved: %s", path.name)
            saved_ids.append(note["id"])
        except Exception as exc:
            log.error("  [FAIL] Failed to write note '%s': %s", note.get("filename"), exc)

    ack_notes(server_url, token, saved_ids, timeout)
    return len(saved_ids)


def pull_and_save() -> int:
    """One-shot: fetch whatever's pending right now, write it, ack it."""
    server_url, token, inbox = get_config()
    if not config_is_valid(server_url, inbox):
        return 0

    endpoint = f"{server_url}/pull-notes"
    log.info("Connecting to %s ...", endpoint)

    data = request_notes(server_url, token, "/pull-notes", CONFIG["request_timeout"])
    if data is None:
        return 0
    if data.get("count", 0) == 0:
        log.info("No pending notes in queue.")
        return 0

    return write_and_ack(data, inbox, server_url, token, CONFIG["request_timeout"])


def watch() -> None:
    """
    Long-lived loop: long-poll /wait-notes so new notes land in the vault
    within seconds, instead of waiting for the next scheduled run.
    Run this instead of scheduling one-shot runs (Ctrl+C to stop).
    """
    server_url, token, inbox = get_config()
    if not config_is_valid(server_url, inbox):
        return

    wait_timeout = CONFIG["wait_timeout"]
    request_timeout = CONFIG["request_timeout"] + wait_timeout
    endpoint = f"{server_url}/wait-notes"
    log.info("Watching %s (long-poll, Ctrl+C to stop) ...", endpoint)

    while True:
        try:
            data = request_notes(
                server_url, token, "/wait-notes", request_timeout, params={"timeout": wait_timeout}
            )
        except KeyboardInterrupt:
            log.info("Stopped.")
            return

        if data is None:
            # Already logged; back off briefly so a down server doesn't spin-loop.
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                log.info("Stopped.")
                return
            continue

        if data.get("count", 0) > 0:
            write_and_ack(data, inbox, server_url, token, CONFIG["request_timeout"])
        # count == 0 just means the long-poll timed out with nothing new — loop again.


# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Obsidian Web Clipper local pull client.")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Long-poll the server continuously instead of doing a single pull.",
    )
    args = parser.parse_args()

    log.info("--- Obsidian Web Clipper - %s (%s) ---",
             "Watch mode" if args.watch else "Pull Run",
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    if args.watch:
        watch()
    else:
        saved = pull_and_save()
        if saved > 0:
            log.info("Done. %d new note(s) added to your Obsidian Inbox.", saved)
        else:
            log.info("Done. Nothing new.")
