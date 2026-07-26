#!/usr/bin/env python3
"""Called daily by .github/workflows/iv-snapshot.yml. Fetches today's ATM IV
per watchlist ticker from the deployed backend and appends each to its own
data/iv-history/{ticker}.json file, which the Action then commits.

This is the only source of IV history this project has: there is no free
API (from Yahoo or anyone else) for historical implied volatility, so the
only way to build a trailing window is to start capturing one data point a
day, starting today. See README.md "IV rank / percentile" for the full
explanation of why this exists as a separate script instead of a database.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

BACKEND_URL = os.environ.get("BACKEND_URL", "https://backend-gules-iota-44.vercel.app")
SECRET = os.environ.get("IV_SNAPSHOT_SECRET", "")
HISTORY_DIR = Path(__file__).resolve().parent.parent / "data" / "iv-history"
MAX_HISTORY_DAYS = 400  # a bit over a year -- keeps files bounded once a real trailing window exists


def main() -> None:
    url = f"{BACKEND_URL}/api/internal/iv-snapshot"
    if SECRET:
        url += f"?secret={SECRET}"

    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.load(resp)

    snapshots = data.get("snapshots", [])
    if not snapshots:
        print("No snapshots returned -- nothing to write.", file=sys.stderr)
        sys.exit(1)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    for snap in snapshots:
        ticker = snap["ticker"]
        path = HISTORY_DIR / f"{ticker}.json"
        history = json.loads(path.read_text()) if path.exists() else []

        if history and history[-1].get("date") == snap["date"]:
            history[-1] = snap  # re-running the same day overwrites instead of duplicating
        else:
            history.append(snap)

        history = history[-MAX_HISTORY_DAYS:]
        path.write_text(json.dumps(history, indent=2) + "\n")
        print(f"{ticker}: {len(history)} days of history (latest: {snap['date']})")


if __name__ == "__main__":
    main()
