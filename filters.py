"""
filters.py

Owns: pre-scoring exclusion rules (Rule 1: follower cap, Rule 2: posting
frequency, Rule 3: brand blocklist) that decide whether a collected record
proceeds to analyzer.py's scoring at all. Does NOT own: scoring
(analyzer.py), the persistent already-sent dedup rule (Rule 4, kept in its
own dedup.py per the task's explicit module-boundary instruction), or any
network I/O.

Each filter function returns True if the record SURVIVES (proceed to the
next filter / to scoring) and False if EXCLUDED.

Deliberately decoupled from analyzer.py -- no import of it. The small
date-parsing helpers below mirror analyzer.py's _parse_date()/
_dated_posts_desc() pattern for consistency (same ISO-date/UTC-default
handling), not reuse; this is a small amount of duplication that's fine
here per the task's explicit instruction to keep these modules decoupled.

Hard rule shared with analyzer.py/collector.py: a field marked
"unavailable" (or otherwise non-numeric/unparseable) means "can't evaluate
this rule", which must mean the record SURVIVES (proceed to other filters /
let analyzer.py's own manual-review flagging surface the data gap) --
never auto-excluded, and never auto-included as if verified.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# Rule 1: follower cap
# ---------------------------------------------------------------------------

FOLLOWER_CAP = 15000


def filter_by_follower_cap(record: dict, max_followers: int = FOLLOWER_CAP) -> bool:
    """
    Exclude (False) only when follower_count is a real number STRICTLY
    GREATER than max_followers. Boundary: exactly max_followers survives.
    follower_count == "unavailable" or any other non-numeric value always
    survives -- insufficient data is never treated as a pass-or-fail signal.
    """
    follower_count = record.get("follower_count", UNAVAILABLE)
    if isinstance(follower_count, bool) or not isinstance(follower_count, (int, float)):
        return True
    return follower_count <= max_followers


# ---------------------------------------------------------------------------
# Rule 2: posting frequency
# ---------------------------------------------------------------------------

MIN_POSTS_TRAILING_30D = 2
POSTING_WINDOW_DAYS = 30


def _parse_date(value: Any) -> datetime | None:
    """Same ISO-date/UTC-default parsing convention as analyzer.py's
    _parse_date() (kept as its own copy here -- see module docstring)."""
    if not isinstance(value, str) or value == UNAVAILABLE:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dated_posts_desc(posts: list[dict]) -> list[datetime]:
    """Dates of posts with a parseable date, sorted newest-first."""
    dated = [dt for p in posts if (dt := _parse_date(p.get("date"))) is not None]
    dated.sort(reverse=True)
    return dated


def filter_by_posting_frequency(
    record: dict,
    min_posts: int = MIN_POSTS_TRAILING_30D,
    window_days: int = POSTING_WINDOW_DAYS,
    now: datetime | None = None,
) -> bool:
    """
    Exclude (False) only if the count of posts dated within `window_days`
    days of `now` is STRICTLY LESS than `min_posts`. Boundary: exactly
    min_posts posts in the window survives.

    If `posts` is "unavailable", not a list, or no post has a parseable
    date at all, this survives (True) -- insufficient DATA is not the same
    as confirmed insufficient posting.
    """
    posts = record.get("posts", UNAVAILABLE)
    if not isinstance(posts, list):
        return True

    dated = _dated_posts_desc(posts)
    if not dated:
        return True

    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    count_in_window = sum(1 for dt in dated if dt >= cutoff)
    return count_in_window >= min_posts


# ---------------------------------------------------------------------------
# Rule 3: brand blocklist
# ---------------------------------------------------------------------------

DEFAULT_BLOCKLIST_PATH = os.path.join(os.path.dirname(__file__), "data", "brand_blocklist.json")


def _normalize(text: str) -> str:
    """Lowercase, strip every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _load_blocklist(path: str) -> list[str]:
    """Load the flat JSON array of brand strings. Tolerates a missing file
    (first-ever setup, or someone deletes it) -- "no blocklist" just means
    "nothing to exclude", same edge-case convention as dedup.py's missing
    history file."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load brand blocklist %s: %s", path, exc)
        return []
    return [b for b in data if isinstance(b, str)] if isinstance(data, list) else []


def filter_by_brand_blocklist(
    record: dict,
    business_name: str | None = None,
    blocklist_path: str = DEFAULT_BLOCKLIST_PATH,
) -> bool:
    """
    Exclude (False) if any seeded brand name is a SUBSTRING of the
    normalized handle OR the normalized business_name. Direction matters:
    brand-inside-name, not name-inside-brand -- this is what stops a short
    blocklist entry from matching every business whose name happens to
    contain it, AS LONG AS data/brand_blocklist.json is curated to contain
    only genuine, distinctive brand names and never a bare generic category
    word (e.g. never a lone "cafe" or "salon" entry -- a bare generic word
    WOULD match virtually everything via this exact substring check, so
    curation discipline on the seed file is what keeps this rule safe, not
    the matching logic itself).

    DELIBERATE, DOCUMENTED LIMITATION: this only catches brand names/handle
    variants actually present in the seed list. It does not attempt fuzzy
    matching or any algorithmic "is this secretly a famous chain" detection
    -- a brand not in the list simply isn't caught. This is not something to
    "improve" with heuristics; it's the intended scope of this rule.
    """
    blocklist = _load_blocklist(blocklist_path)
    if not blocklist:
        return True

    handle = record.get("handle", UNAVAILABLE)
    handle_norm = _normalize(handle) if isinstance(handle, str) and handle != UNAVAILABLE else ""
    name_norm = _normalize(business_name) if isinstance(business_name, str) else ""

    for brand in blocklist:
        brand_norm = _normalize(brand)
        if not brand_norm:
            continue
        if (handle_norm and brand_norm in handle_norm) or (name_norm and brand_norm in name_norm):
            return False
    return True
