"""Vercel Python Functions entrypoint. Vercel scans backend/api/*.py and
treats each file's `app` export as a serverless function; since ours is an
ASGI app (FastAPI), Vercel's Python runtime wraps it accordingly.
"""

from app.main import app  # noqa: F401
