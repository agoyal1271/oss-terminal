"""Simple disk-backed JSON GET cache with per-call TTL.

Keeps us polite towards free public APIs (SEC asks for a max of ~10 req/s
and strongly prefers callers cache aggressively; Yahoo's unofficial endpoint
has no published limit but is easy to get rate-limited on). Every response is
written to a flat JSON file keyed by a hash of the URL, wrapped with a
fetched_at timestamp so staleness is explicit and inspectable on disk.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings


class UpstreamError(RuntimeError):
    """Raised when an upstream data source fails or returns something unusable."""


def _cache_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    d = settings.cache_dir / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def _read_cache(path: Path, ttl: int) -> Any | None:
    if not path.exists():
        return None
    try:
        wrapper = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - wrapper.get("fetched_at", 0) > ttl:
        return None
    return wrapper.get("data")


def _write_cache(path: Path, data: Any) -> None:
    wrapper = {"fetched_at": time.time(), "data": data}
    path.write_text(json.dumps(wrapper))


def cached_get_json(
    namespace: str,
    url: str,
    ttl: int,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    """GET a URL and cache the parsed JSON response on disk for `ttl` seconds."""
    cache_key = url + ("?" + json.dumps(params, sort_keys=True) if params else "")
    path = _cache_path(namespace, cache_key)

    cached = _read_cache(path, ttl)
    if cached is not None:
        return cached

    try:
        resp = httpx.get(url, headers=headers, params=params, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        # Serve stale cache rather than fail hard, if we have anything at all.
        if path.exists():
            try:
                stale = json.loads(path.read_text())
                return stale.get("data")
            except (json.JSONDecodeError, OSError):
                pass
        raise UpstreamError(f"failed to fetch {url}: {exc}") from exc

    _write_cache(path, data)
    return data
