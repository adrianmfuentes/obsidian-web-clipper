"""
Set required env vars before main.py is imported anywhere in the test session —
main.py reads AUTH_TOKEN / GEMINI_API_KEY at module import time with no default.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AUTH_TOKEN", "test-auth-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
