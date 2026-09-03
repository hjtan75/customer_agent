"""Early Risers promotion logic.

The time-window check and code generation live in code (not the prompt) so
the model can't be talked into issuing a discount outside 8-10 AM Pacific.
"""

from __future__ import annotations

import secrets
from datetime import datetime, time
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
PROMO_START = time(8, 0)
PROMO_END = time(10, 0)
DISCOUNT_PERCENT = 10


def is_promo_active(now: datetime | None = None) -> bool:
    """True if the current Pacific time is within the 8:00-10:00 AM window."""
    now = now or datetime.now(PACIFIC)
    local = now.astimezone(PACIFIC).time()
    return PROMO_START <= local < PROMO_END


def generate_code() -> str:
    """A unique, hard-to-guess single-use code."""
    return f"EARLYRISER-{secrets.token_hex(3).upper()}"
