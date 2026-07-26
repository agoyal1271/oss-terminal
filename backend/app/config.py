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

    class Config:
        env_file = ".env"


settings = Settings()
settings.cache_dir.mkdir(parents=True, exist_ok=True)
