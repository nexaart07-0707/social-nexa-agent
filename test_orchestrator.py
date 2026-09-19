"""
Tests for orchestrator.py (integration glue).

All six real modules' functions are mocked here -- these tests verify
orchestrator.py's own sequencing/failure-isolation logic, not the modules
themselves (each module has its own passing test suite already).
"""

from __future__ import annotations

import os
from datetime import date as _date
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import orchestrator
import session_manager


IST_RUN1_TIME = datetime(2026, 9, 19, 14, 0, 0, tzinfo=timezone.utc)  # arbitrary tz-aware dt


def _candidate(handle="somehandle", niche="cafes/bakeries/home-food"):
    return {
        "name": "Test Biz",
        "handle_guess": handle,
        "address": "Somewhere",
        "lat": 1.0,
        "lon": 1.0,
        "niche": niche,
        "chain_group_id": None,
    }


def _record(handle="somehandle"):
    return {
        "handle": handle,
        "bio": "unavailable",
        "follower_count": "unavailable",
        "following_count": "unavailable",
        "posts": "unavailable",
        "highlight_count": "unavailable",
        "video_count": "unavailable",
        "image_count": "unavailable",
    }


# ---------------------------------------------------------------------------
# (a) no active window, no override -> exits early, calls nothing else
# ---------------------------------------------------------------------------


def test_no_active_window_exits_early_without_calling_anything_else():
    with patch("orchestrator.scheduler_rules.get_window_for_time", return_value=None) as mock_window, \
         patch("orchestrator.session_manager.ensure_session") as mock_session, \
         patch("orchestrator.discovery.discover_for_run") as mock_discovery, \
         patch("orchestrator.collector.collect_many") as mock_collect, \
         patch("orchestrator.storage.write_leads_csv") as mock_storage, \
         patch("orchestrator.notifier.notify_run_complete") as mock_notify:
        summary = orchestrator.run(run_name=None, dry_run=False)

    mock_window.assert_called_once()
    mock_session.assert_not_called()
    mock_discovery.assert_not_called()
    mock_collect.assert_not_called()
    mock_storage.assert_not_called()
    mock_notify.assert_not_called()
    assert summary == {
        "candidates_found": 0,
        "records_collected": 0,
        "records_scored": 0,
        "csv_path": None,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# (b) session_manager halt exception -> stops immediately, notifies, and
#     never calls discovery/collector/analyzer/storage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_cls",
    [
        session_manager.SessionInvalidError,
        session_manager.ChallengeRequiredError,
        session_manager.SessionManagerError,
    ],
)
def test_session_halt_stops_run_and_notifies(exc_cls):
    with patch("orchestrator.session_manager.ensure_session", side_effect=exc_cls("boom")), \
         patch("orchestrator.discovery.discover_for_run") as mock_discovery, \
         patch("orchestrator.collector.collect_many") as mock_collect, \
         patch("orchestrator.analyzer.analyze") as mock_analyze, \
         patch("orchestrator.storage.write_leads_csv") as mock_storage, \
         patch("orchestrator.notifier.notify_run_complete") as mock_notify:
        summary = orchestrator.run(run_name="run1", dry_run=False)

    mock_discovery.assert_not_called()
    mock_collect.assert_not_called()
    mock_analyze.assert_not_called()
    mock_storage.assert_not_called()
    mock_notify.assert_called_once()
    # notify_run_complete(csv_path=None, records=[], errors=[<message>])
    _, kwargs = mock_notify.call_args
    call_args = mock_notify.call_args.args
    all_args = call_args + tuple(kwargs.values())
    assert None in all_args or mock_notify.call_args.kwargs.get("csv_path") is None
    assert summary["csv_path"] is None
    assert summary["candidates_found"] == 0
    assert summary["records_collected"] == 0
    assert summary["records_scored"] == 0
    assert len(summary["errors"]) == 1


# ---------------------------------------------------------------------------
# (c) discovery failure doesn't crash the run: continues with empty
#     candidates, still completes and calls storage/notifier with zero
#     records
# ---------------------------------------------------------------------------


def test_discovery_failure_continues_with_empty_candidates():
    with patch("orchestrator.session_manager.ensure_session", return_value=None), \
         patch("orchestrator.session_manager.get_loader", return_value=object()), \
         patch("orchestrator.discovery.discover_for_run", side_effect=RuntimeError("nominatim down")), \
         patch("orchestrator.collector.collect_many") as mock_collect, \
         patch("orchestrator.storage.write_leads_csv", return_value="output/leads_x.csv") as mock_storage, \
         patch("orchestrator.notifier.notify_run_complete", return_value=False) as mock_notify:
        summary = orchestrator.run(run_name="run1", dry_run=False)

    mock_collect.assert_not_called()
    mock_storage.assert_called_once_with([])
    mock_notify.assert_called_once_with("output/leads_x.csv", [], summary["errors"])
    assert summary["candidates_found"] == 0
    assert summary["records_collected"] == 0
    assert summary["records_scored"] == 0
    assert summary["csv_path"] == "output/leads_x.csv"
    assert any("discovery failed" in str(e) for e in summary["errors"])


# ---------------------------------------------------------------------------
# (d) full happy-path run: every step called in order, correct summary dict
# ---------------------------------------------------------------------------


def test_happy_path_calls_every_step_in_order_and_builds_summary():
    candidates = [_candidate("handleone", "cafes/bakeries/home-food"),
                  _candidate("handletwo", "tutors/coaching")]
    records = [_record("handleone"), _record("handletwo")]
    analyzed = [{"handle": "handleone", "niche": "cafes/bakeries/home-food",
                 "category_scores": {}, "total_score": 5, "flags": [], "manual_review": [],
                 "profile_metrics": {}, "data_quality": {}},
                {"handle": "handletwo", "niche": "tutors/coaching",
                 "category_scores": {}, "total_score": 3, "flags": [], "manual_review": [],
                 "profile_metrics": {}, "data_quality": {}}]

    manager = []

    def track(name):
        def _inner(*a, **k):
            manager.append(name)
        return _inner

    with patch("orchestrator.session_manager.ensure_session", side_effect=track("session")), \
         patch("orchestrator.session_manager.get_loader", return_value=object()), \
         patch("orchestrator.discovery.discover_for_run", side_effect=lambda *a, **k: (track("discovery")(), candidates)[1]), \
         patch("orchestrator.collector.collect_many", side_effect=lambda *a, **k: (track("collect")(), records)[1]), \
         patch("orchestrator.analyzer.analyze", side_effect=lambda rec, niche, **k: (track("analyze")(), analyzed[[r["handle"] for r in records].index(rec["handle"])])[1]), \
         patch("orchestrator.storage.write_leads_csv", side_effect=lambda *a, **k: (track("storage")(), "output/leads_y.csv")[1]) as mock_storage, \
         patch("orchestrator.notifier.notify_run_complete", side_effect=lambda *a, **k: (track("notify")(), True)[1]) as mock_notify:
        summary = orchestrator.run(run_name="run1", dry_run=False)

    assert manager == ["session", "discovery", "collect", "analyze", "analyze", "storage", "notify"]

    # storage got (candidate, analyzed) pairs
    storage_call_rows = mock_storage.call_args.args[0]
    assert len(storage_call_rows) == 2
    assert storage_call_rows[0][0]["handle_guess"] == "handleone"
    assert storage_call_rows[0][1]["handle"] == "handleone"

    # notifier got the analyzed records list
    notify_args = mock_notify.call_args.args
    assert notify_args[0] == "output/leads_y.csv"
    assert len(notify_args[1]) == 2

    assert summary == {
        "candidates_found": 2,
        "records_collected": 2,
        "records_scored": 2,
        "csv_path": "output/leads_y.csv",
        "errors": [],
    }


# ---------------------------------------------------------------------------
# (e) one bad record among several good ones doesn't stop the others from
#     being scored/written
# ---------------------------------------------------------------------------


def test_one_bad_record_does_not_stop_others_from_being_scored():
    candidates = [_candidate("good1", "cafes/bakeries/home-food"),
                  _candidate("bad", "restaurants"),
                  _candidate("good2", "salons/spas/beauty")]
    records = [_record("good1"), _record("bad"), _record("good2")]

    def fake_analyze(record, niche, **kwargs):
        if record["handle"] == "bad":
            raise ValueError("simulated malformed record")
        return {"handle": record["handle"], "niche": niche, "category_scores": {},
                "total_score": 1, "flags": [], "manual_review": [], "profile_metrics": {},
                "data_quality": {}}

    with patch("orchestrator.session_manager.ensure_session", return_value=None), \
         patch("orchestrator.session_manager.get_loader", return_value=object()), \
         patch("orchestrator.discovery.discover_for_run", return_value=candidates), \
         patch("orchestrator.collector.collect_many", return_value=records), \
         patch("orchestrator.analyzer.analyze", side_effect=fake_analyze), \
         patch("orchestrator.storage.write_leads_csv", return_value="output/leads_z.csv") as mock_storage, \
         patch("orchestrator.notifier.notify_run_complete", return_value=False) as mock_notify:
        summary = orchestrator.run(run_name="run1", dry_run=False)

    storage_rows = mock_storage.call_args.args[0]
    scored_handles = [pair[1]["handle"] for pair in storage_rows]
    assert scored_handles == ["good1", "good2"]

    notify_records = mock_notify.call_args.args[1]
    assert len(notify_records) == 2

    assert summary["candidates_found"] == 3
    assert summary["records_collected"] == 3
    assert summary["records_scored"] == 2
    assert len(summary["errors"]) == 1
    assert "bad" in summary["errors"][0]


# ---------------------------------------------------------------------------
# (f) --report mode: find_todays_run_csvs() classifies filenames by window
# ---------------------------------------------------------------------------


def _write_csv(output_dir, report_date, hhmm):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"leads_{report_date.isoformat()}_{hhmm}.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("handle\n")
    return path


def test_find_todays_run_csvs_classifies_by_utc_window(tmp_path):
    report_date = _date(2026, 9, 19)
    output_dir = str(tmp_path)

    # run1 window: 07:30-11:30 UTC (= 1PM-5PM IST)
    run1_path = _write_csv(output_dir, report_date, "0800")
    # run2 window: 11:30-14:30 UTC (= 5PM-7PM IST)
    run2_path = _write_csv(output_dir, report_date, "1200")
    # outside both windows -- must be ignored
    _write_csv(output_dir, report_date, "2000")

    found = orchestrator.find_todays_run_csvs(output_dir=output_dir, report_date=report_date)

    assert found["run1"] == run1_path
    assert found["run2"] == run2_path


def test_find_todays_run_csvs_picks_latest_when_multiple_in_same_window(tmp_path):
    report_date = _date(2026, 9, 19)
    output_dir = str(tmp_path)

    _write_csv(output_dir, report_date, "0730")
    latest = _write_csv(output_dir, report_date, "0900")

    found = orchestrator.find_todays_run_csvs(output_dir=output_dir, report_date=report_date)
    assert found["run1"] == latest


def test_find_todays_run_csvs_missing_files_returns_none():
    found = orchestrator.find_todays_run_csvs(
        output_dir="output_dir_that_does_not_exist", report_date=_date(2026, 9, 19)
    )
    assert found == {"run1": None, "run2": None}


# ---------------------------------------------------------------------------
# (g) --report mode: run_report() glue
# ---------------------------------------------------------------------------


def test_run_report_missing_csv_sends_empty_list_for_that_run():
    """Per the task's own instruction: a run whose CSV isn't found by cutoff
    still calls send_daily_email_report with an empty list, reusing
    notifier's existing zero-results handling -- no new empty-state logic."""
    report_date = _date(2026, 9, 19)
    with patch(
        "orchestrator.find_todays_run_csvs",
        return_value={"run1": "output/leads_2026-09-19_0800.csv", "run2": None},
    ), \
         patch("orchestrator.notifier.load_scored_records_from_csv", return_value=[{"handle": "a"}]) as mock_load, \
         patch("orchestrator.notifier.send_daily_email_report", return_value=True) as mock_send:
        results = orchestrator.run_report(report_date=report_date)

    mock_load.assert_called_once_with("output/leads_2026-09-19_0800.csv")
    assert mock_send.call_count == 2
    run1_call = mock_send.call_args_list[0]
    run2_call = mock_send.call_args_list[1]
    assert run1_call.args == ("run1", [{"handle": "a"}], report_date)
    assert run2_call.args == ("run2", [], report_date)

    assert results["run1"] == {"csv_path": "output/leads_2026-09-19_0800.csv", "record_count": 1, "sent": True}
    assert results["run2"] == {"csv_path": None, "record_count": 0, "sent": True}


def test_run_report_load_failure_reports_empty_but_does_not_crash():
    report_date = _date(2026, 9, 19)
    with patch(
        "orchestrator.find_todays_run_csvs",
        return_value={"run1": "output/corrupt.csv", "run2": None},
    ), \
         patch("orchestrator.notifier.load_scored_records_from_csv", side_effect=ValueError("bad csv")), \
         patch("orchestrator.notifier.send_daily_email_report", return_value=False) as mock_send:
        results = orchestrator.run_report(report_date=report_date)

    # send_daily_email_report is still called with an empty list, not skipped
    run1_call = mock_send.call_args_list[0]
    assert run1_call.args == ("run1", [], report_date)
    assert "error" in results["run1"]
    assert results["run1"]["record_count"] == 0


def test_run_report_both_csvs_found_loads_and_sends_both():
    report_date = _date(2026, 9, 19)
    csvs = {"run1": "output/r1.csv", "run2": "output/r2.csv"}
    with patch("orchestrator.find_todays_run_csvs", return_value=csvs), \
         patch("orchestrator.notifier.load_scored_records_from_csv", side_effect=lambda p: [{"csv": p}]) as mock_load, \
         patch("orchestrator.notifier.send_daily_email_report", return_value=True) as mock_send:
        results = orchestrator.run_report(report_date=report_date)

    assert mock_load.call_count == 2
    assert mock_send.call_count == 2
    assert results["run1"]["record_count"] == 1
    assert results["run2"]["record_count"] == 1


def test_cli_report_flag_invokes_run_report_and_exits_zero():
    with patch("orchestrator.run_report", return_value={"run1": {}, "run2": {}}) as mock_report, \
         patch("orchestrator.run") as mock_run:
        exit_code = orchestrator.main(["--report"])

    mock_report.assert_called_once()
    mock_run.assert_not_called()
    assert exit_code == 0
