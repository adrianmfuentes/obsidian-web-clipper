"""
Set required env vars before main.py is imported anywhere in the test session —
main.py reads AUTH_TOKEN / GEMINI_API_KEY at module import time with no default.

DATA_DIR must also be overridden before database.py is imported: its default
(/app/data) is only writable inside the Docker image. Module-scoped fixtures
like test_main.py's `client` run their TestClient lifespan (and thus
database.init_db()) before any per-test fixture gets a chance to monkeypatch
DB_PATH, so without this, tests crash with PermissionError on any machine
where /app isn't a writable path owned by the current user (e.g. real Linux
CI runners — this exact gap once passed locally on Windows, where "/app/data"
just resolves to a writable relative path, and broke the real GitHub Actions
run).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AUTH_TOKEN", "test-auth-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obsidian-clipper-test-"))
