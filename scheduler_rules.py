"""
Module 6 — Scheduler rules.

Owns: window timing (Run 1 / Run 2 IST windows), min/max qualifying-result
enforcement, explicit server-timezone verification, and crontab documentation.

Does NOT own: the business logic of discovery/collection/scoring, and does
NOT own the Hubli-vs-nationwide location split (PRD.md §5) — that's Module
2/4's concern. This module only ever looks at a TOTAL qualifying count and
an IST-aware datetime.

There is no orchestrator.py yet (Module 6 is the last module built before
integration), so everything here is a pure function or a simple I/O-free
utility that can be exercised with synthetic inputs — see
test_scheduler_rules.py for the dry-run simulation required by TRD.md's DoD
for this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, TypedDict

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.9+ always has zoneinfo
    raise

IST = ZoneInfo("Asia/Kolkata")

Window = Literal["run1", "run2"]


# ---------------------------------------------------------------------------
# 1. Window resolution
# ---------------------------------------------------------------------------
#
# Boundary convention (the PRD only says "1:00 PM-5:00 PM" / "5:00 PM-7:00 PM"
# without stating which end is inclusive/exclusive — we pick and document one
# consistent rule):
#
#   run1 = [13:00:00, 17:00:00) IST   -- start inclusive, end exclusive
#   run2 = [17:00:00, 19:00:00) IST   -- start inclusive, end exclusive
#
# Rationale: half-open intervals make the two windows partition the day
# cleanly with no double-counted instant and no gap — 17:00:00.000000 IST
# belongs to run2, not run1. This also matches how cron naturally behaves:
# a job fired "at 17:00" is the *start* of the next window, not the tail end
# of the previous one.


def get_window_for_time(dt_ist: datetime) -> Optional[Window]:
    """Return which scheduling window an IST-aware datetime falls in.

    Args:
        dt_ist: a timezone-aware datetime. Must carry tzinfo; it is
            converted to IST if it isn't already in that zone (so a
            correctly-labelled UTC or other-zone datetime still resolves
            correctly — only naive datetimes are rejected, since a naive
            value carries no proof of what zone it's in).

    Returns:
        "run1", "run2", or None if outside both windows.
    """
    if dt_ist.tzinfo is None:
        raise ValueError(
            "get_window_for_time requires a timezone-aware datetime; "
            "naive datetimes are rejected because their zone is unknown "
            "(see TRD.md: never assume a timezone without checking)."
        )

    local = dt_ist.astimezone(IST)
    t = local.time()

    run1_start, run1_end = _time(13, 0, 0), _time(17, 0, 0)
    run2_start, run2_end = _time(17, 0, 0), _time(19, 0, 0)

    if run1_start <= t < run1_end:
        return "run1"
    if run2_start <= t < run2_end:
        return "run2"
    return None


def _time(hour: int, minute: int, second: int):
    from datetime import time

    return time(hour, minute, second)


# ---------------------------------------------------------------------------
# 2. Min/max enforcement
# ---------------------------------------------------------------------------

class MinMaxResult(TypedDict):
    ok: bool
    reason: Optional[str]


_RULES = {
    "run1": {"min": 2, "max": None},
    "run2": {"min": 2, "max": 3},
}


def check_min_max(run: Window, qualifying_count: int) -> MinMaxResult:
    """Check a qualifying-result count against the run's confirmed min/max
    (PRD.md §5): run1 >= 2 (no max), run2 in [2, 3].
    """
    if run not in _RULES:
        raise ValueError(f"unknown run: {run!r}")

    rules = _RULES[run]
    min_required = rules["min"]
    max_allowed = rules["max"]

    if qualifying_count < min_required:
        return {
            "ok": False,
            "reason": (
                f"only {qualifying_count} qualifying result"
                f"{'s' if qualifying_count != 1 else ''}, "
                f"need at least {min_required}"
            ),
        }

    if max_allowed is not None and qualifying_count > max_allowed:
        return {
            "ok": False,
            "reason": (
                f"{qualifying_count} qualifying results exceeds the max "
                f"of {max_allowed}"
            ),
        }

    return {"ok": True, "reason": None}


# ---------------------------------------------------------------------------
# 3. Explicit timezone conversion / verification
# ---------------------------------------------------------------------------

def is_report_time(dt_ist: datetime) -> bool:
    """Return True if `dt_ist` falls in the daily email-report trigger window.

    The daily email report (Module 5 extension, notifier.py) fires once a
    day at 8:00 PM IST, independent of the run1/run2 research windows. Cron
    firing precision is not exact to the second, so this uses a small
    tolerance window rather than an exact-minute match: [20:00:00, 20:05:00)
    IST, half-open like the run windows above. This absorbs a few minutes of
    scheduler jitter without risking spilling into an adjacent hour.

    Args:
        dt_ist: a timezone-aware datetime, converted to IST if not already
            (same convention as get_window_for_time; naive datetimes raise).
    """
    if dt_ist.tzinfo is None:
        raise ValueError(
            "is_report_time requires a timezone-aware datetime; "
            "naive datetimes are rejected because their zone is unknown."
        )

    local = dt_ist.astimezone(IST)
    t = local.time()
    return _time(20, 0, 0) <= t < _time(20, 5, 0)


def now_ist() -> datetime:
    """Current time, explicitly converted to Asia/Kolkata via the real IANA
    tzdata (zoneinfo) — never a hardcoded UTC+5:30 offset. IST currently has
    no DST, but using the real tz key (rather than a fixed offset) is what
    makes this correct-by-construction rather than correct-by-assumption,
    which is exactly what TRD.md's "do not assume ... without checking"
    requirement is about.
    """
    return datetime.now(IST)


def verify_server_timezone_handling() -> dict:
    """Self-check for a human/manager to visually confirm on the real
    deployment VM that the IST conversion is correct, regardless of what
    timezone the server itself defaults to (UTC is common on cloud VMs).

    Returns both the server's own detected local time/zone AND the computed
    IST time side by side.
    """
    server_local = datetime.now().astimezone()  # server's own local tz
    utc_now = datetime.now(timezone.utc)
    ist_now = now_ist()

    return {
        "server_local_time": server_local.isoformat(),
        "server_local_tz_name": server_local.tzname(),
        "server_utc_offset": str(server_local.utcoffset()),
        "utc_time": utc_now.isoformat(),
        "computed_ist_time": ist_now.isoformat(),
        "ist_tz_key": "Asia/Kolkata",
        "ist_utc_offset": str(ist_now.utcoffset()),
    }


# ---------------------------------------------------------------------------
# 4. Crontab documentation
# ---------------------------------------------------------------------------
#
# cron runs jobs in the SERVER's local time, not IST, unless the server's
# local time already IS IST. There are two deployment scenarios:
#
#   (a) VM system timezone is set to Asia/Kolkata:
#         0 13 * * * /path/to/venv/bin/python /path/to/orchestrator.py  # Run 1, 1:00 PM IST
#         0 17 * * * /path/to/venv/bin/python /path/to/orchestrator.py  # Run 2, 5:00 PM IST
#
#   (b) VM runs in UTC (the default on most cloud images, e.g. Oracle Cloud
#       Always Free) — IST is UTC+5:30, so 1:00 PM IST = 07:30 UTC and
#       5:00 PM IST = 11:30 UTC:
#         30 7  * * * /path/to/venv/bin/python /path/to/orchestrator.py   # Run 1, 1:00 PM IST = 07:30 UTC
#         30 11 * * * /path/to/venv/bin/python /path/to/orchestrator.py   # Run 2, 5:00 PM IST = 11:30 UTC
#
# RECOMMENDATION: prefer (a) — explicitly set the VM's system timezone to
# Asia/Kolkata (e.g. `sudo timedatectl set-timezone Asia/Kolkata` on most
# Linux distros), OR, if that's not possible/desirable, use `CRON_TZ=Asia/Kolkata`
# (or `TZ=Asia/Kolkata`, depending on the cron implementation — both Vixie
# cron and most cronie builds support one of these) as a line in the
# crontab itself:
#
#         CRON_TZ=Asia/Kolkata
#         0 13 * * * /path/to/venv/bin/python /path/to/orchestrator.py
#         0 17 * * * /path/to/venv/bin/python /path/to/orchestrator.py
#
# Daily email report (notifier.py: send_daily_email_report), 8:00 PM IST —
# a separate trigger point from run1/run2, added the same way:
#
#   (a) VM system timezone is Asia/Kolkata:
#         0 20 * * * /path/to/venv/bin/python /path/to/daily_report.py   # 8:00 PM IST
#
#   (b) VM runs in UTC — 8:00 PM IST = UTC + 5:30 => subtract 5:30 from
#       20:00 IST to get 14:30 UTC:
#         30 14 * * * /path/to/venv/bin/python /path/to/daily_report.py  # 8:00 PM IST = 14:30 UTC
#
#   Or, with CRON_TZ=Asia/Kolkata already set in the crontab (recommended,
#   see above), the same line as (a) works regardless of VM system tz:
#         0 20 * * * /path/to/venv/bin/python /path/to/daily_report.py
#
# Do NOT rely on the plain UTC math (option b) as the long-term setup: it is
# a silent half-hour-offset bug risk — e.g. a VM image update, a
# misconfigured NTP/timezone reset, or a copy-paste to a different server
# will desync the cron times from IST with no error, no crash, and no
# alert. Always call verify_server_timezone_handling() after deployment (or
# after any VM change) to visually confirm the conversion before trusting
# the schedule.
