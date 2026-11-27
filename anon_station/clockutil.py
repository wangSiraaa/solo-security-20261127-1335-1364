"""Clock helpers.

``ANON_STATION_NOW`` makes TTL demonstrations deterministic without sleeping.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone


def now() -> int:
    forced = os.environ.get("ANON_STATION_NOW")
    if forced:
        return int(float(forced))
    return int(time.time())


def iso(ts: int | float | None = None) -> str:
    value = now() if ts is None else int(ts)
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
