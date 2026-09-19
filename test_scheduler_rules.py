"""
Tests for Module 6 — Scheduler rules.

This is the "dry-run simulation" TRD.md's DoD asks for: synthetic clock
times and synthetic qualifying-result counts are fed through
get_window_for_time() and check_min_max() to prove the window and min/max
logic trigger correctly at real boundary values — no real clock, no
orchestrator required.
"""

from datetime import datetime, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

from scheduler_rules import (
    IST,
    check_min_max,
    get_window_for_time,
    is_report_time,
    now_ist,
    verify_server_timezone_handling,
)


def ist(hour, minute, second=0, day=1):
    return datetime(2026, 1, day, hour, minute, second, tzinfo=IST)


# ---------------------------------------------------------------------------
# get_window_for_time — boundary values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "dt, expected",
    [
        # well before run1
        (ist(9, 0, 0), None),
        # just before run1 start
        (ist(12, 59, 59), None),
        # exactly run1 start (inclusive)
        (ist(13, 0, 0), "run1"),
        # well inside run1
        (ist(15, 0, 0), "run1"),
        # just before run1 end
        (ist(16, 59, 59), "run1"),
        # exactly run1 end == run2 start (run1 end exclusive, run2 start inclusive)
        (ist(17, 0, 0), "run2"),
        # well inside run2
        (ist(18, 0, 0), "run2"),
        # just before run2 end
        (ist(18, 59, 59), "run2"),
        # exactly run2 end (exclusive -> outside both)
        (ist(19, 0, 0), None),
        # well after run2
        (ist(21, 0, 0), None),
        # midnight / well outside
        (ist(0, 0, 0), None),
    ],
)
def test_get_window_for_time_boundaries(dt, expected):
    assert get_window_for_time(dt) == expected


def test_get_window_for_time_rejects_naive_datetime():
    naive = datetime(2026, 1, 1, 14, 0, 0)
    with pytest.raises(ValueError):
        get_window_for_time(naive)


def test_get_window_for_time_converts_non_ist_tz_correctly():
    # 08:30 UTC == 14:00 IST (UTC+5:30) -> should resolve to run1
    dt_utc = datetime(2026, 1, 1, 8, 30, 0, tzinfo=timezone.utc)
    assert get_window_for_time(dt_utc) == "run1"

    # 11:30 UTC == 17:00 IST -> should resolve to run2 (half-open boundary)
    dt_utc2 = datetime(2026, 1, 1, 11, 30, 0, tzinfo=timezone.utc)
    assert get_window_for_time(dt_utc2) == "run2"


# ---------------------------------------------------------------------------
# check_min_max — run1 (min 2, no max)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "count, expected_ok",
    [
        (0, False),
        (1, False),
        (2, True),
        (3, True),
        (100, True),  # no max for run1
    ],
)
def test_check_min_max_run1(count, expected_ok):
    result = check_min_max("run1", count)
    assert result["ok"] is expected_ok
    if not expected_ok:
        assert "need at least 2" in result["reason"]
    else:
        assert result["reason"] is None


# ---------------------------------------------------------------------------
# check_min_max — run2 (min 2, max 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "count, expected_ok",
    [
        (0, False),
        (1, False),
        (2, True),
        (3, True),
        (4, False),
        (5, False),
    ],
)
def test_check_min_max_run2(count, expected_ok):
    result = check_min_max("run2", count)
    assert result["ok"] is expected_ok
    if not expected_ok:
        assert result["reason"] is not None
    else:
        assert result["reason"] is None


def test_check_min_max_run2_reason_text_below_min():
    result = check_min_max("run2", 1)
    assert "need at least 2" in result["reason"]


def test_check_min_max_run2_reason_text_above_max():
    result = check_min_max("run2", 4)
    assert "exceeds the max of 3" in result["reason"]


def test_check_min_max_unknown_run_raises():
    with pytest.raises(ValueError):
        check_min_max("run3", 2)


# ---------------------------------------------------------------------------
# now_ist / verify_server_timezone_handling
# ---------------------------------------------------------------------------

def test_now_ist_is_timezone_aware_and_correct_zone():
    result = now_ist()
    assert result.tzinfo is not None
    # Prove we used the real IANA zone, not a hardcoded +5:30 offset,
    # by comparing zone identity (not just the numeric offset).
    assert result.tzinfo == ZoneInfo("Asia/Kolkata")


def test_now_ist_matches_utc_now_converted():
    ist_now = now_ist()
    utc_now = datetime.now(timezone.utc)
    # should agree within a couple seconds of each other
    diff = abs((ist_now.astimezone(timezone.utc) - utc_now).total_seconds())
    assert diff < 5


def test_verify_server_timezone_handling_runs_and_returns_both_pieces():
    info = verify_server_timezone_handling()
    assert "server_local_time" in info
    assert "server_local_tz_name" in info
    assert "computed_ist_time" in info
    assert "ist_tz_key" in info
    assert info["ist_tz_key"] == "Asia/Kolkata"
    assert info["ist_utc_offset"] == "5:30:00"


# ---------------------------------------------------------------------------
# Combined dry-run simulation (per TRD.md DoD wording)
# ---------------------------------------------------------------------------

def test_is_report_time_rejects_naive_datetime():
    naive = datetime(2026, 1, 1, 20, 0, 0)
    with pytest.raises(ValueError):
        is_report_time(naive)


@pytest.mark.parametrize(
    "dt, expected",
    [
        (ist(19, 59, 59), False),   # just before window
        (ist(20, 0, 0), True),      # exactly 8:00 PM IST (inclusive)
        (ist(20, 2, 30), True),     # mid-tolerance-window
        (ist(20, 4, 59), True),     # just before window end
        (ist(20, 5, 0), False),     # exactly window end (exclusive)
        (ist(20, 30, 0), False),    # well after window
        (ist(0, 0, 0), False),      # midnight
    ],
)
def test_is_report_time_boundaries(dt, expected):
    assert is_report_time(dt) is expected


def test_is_report_time_converts_non_ist_tz_correctly():
    # 14:30 UTC == 20:00 IST -> should be True
    dt_utc = datetime(2026, 1, 1, 14, 30, 0, tzinfo=timezone.utc)
    assert is_report_time(dt_utc) is True

    # 15:00 UTC == 20:30 IST -> should be False
    dt_utc2 = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
    assert is_report_time(dt_utc2) is False


def test_dry_run_simulation_full_scenario():
    """Simulate a full day with synthetic (window, count) events and assert
    the combined window + min/max pipeline behaves as PRD.md §5 requires.
    """
    scenarios = [
        # (synthetic ist datetime, synthetic qualifying count, expected window, expected ok)
        (ist(12, 0), 5, None, None),          # outside any window
        (ist(13, 0), 1, "run1", False),        # run1 start, under min
        (ist(13, 0), 2, "run1", True),         # run1 start, meets min
        (ist(16, 59, 59), 10, "run1", True),   # run1 tail, no max cap
        (ist(17, 0), 2, "run2", True),         # run2 start, meets min
        (ist(17, 0), 4, "run2", False),        # run2 start, exceeds max
        (ist(18, 59, 59), 3, "run2", True),    # run2 tail, at max
        (ist(19, 0), 3, None, None),           # just past run2 end
    ]

    for dt, count, expected_window, expected_ok in scenarios:
        window = get_window_for_time(dt)
        assert window == expected_window, f"window mismatch at {dt}"
        if window is None:
            continue
        result = check_min_max(window, count)
        assert result["ok"] is expected_ok, f"min/max mismatch at {dt} count={count}"
