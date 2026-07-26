import os
from pathlib import Path

from pydantic_settings import BaseSettings

# Vercel (and most serverless platforms) ship a read-only deployment bundle --
# only /tmp is writable, and it isn't guaranteed to persist between
# invocations. Fall back to it automatically instead of failing to start.
_DEFAULT_CACHE_DIR = (
    Path("/tmp/oss-terminal-cache")
    if os.environ.get("VERCEL")
    else Path(__file__).resolve().parent.parent / "data" / "cache"
)


class Settings(BaseSettings):
    # SEC requires a descriptive User-Agent with contact info on every request.
    # https://www.sec.gov/os/webmaster-faq#developers
    sec_user_agent: str = "OSS-Terminal research-tool contact:garchit1988@gmail.com"

    cache_dir: Path = _DEFAULT_CACHE_DIR

    # TTLs, in seconds
    ttl_ticker_universe: int = 24 * 3600
    ttl_company_facts: int = 12 * 3600
    ttl_submissions: int = 6 * 3600
    ttl_prices: int = 15 * 60

    fred_api_key: str | None = None

    # Shared secret the daily GitHub Action snapshot job must present to
    # /internal/iv-snapshot. If unset (local dev), the endpoint is open --
    # set it in Vercel + as a GitHub Actions secret before relying on this
    # in production.
    iv_snapshot_secret: str | None = None

    # Where the daily-committed IV history JSON files live -- read live from
    # GitHub's raw content CDN rather than bundled into the deployment, so
    # new data shows up without a redeploy.
    iv_history_repo_raw_base: str = "https://raw.githubusercontent.com/agoyal1271/oss-terminal/main/data/iv-history"

    class Config:
        env_file = ".env"


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)
