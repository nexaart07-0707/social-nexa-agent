import json
import os
from datetime import date

import dedup


def _record(handle):
    return {"handle": handle, "follower_count": 100, "posts": [], "bio": "unavailable"}


# ---------------------------------------------------------------------------
# load_sent_history -- missing/corrupt file edge cases
# ---------------------------------------------------------------------------


def test_load_sent_history_missing_file_returns_empty(tmp_path):
    """A missing history file (first-ever run) -> {} , never an error."""
    missing = str(tmp_path / "nope.json")
    assert dedup.load_sent_history(missing) == {}


def test_load_sent_history_corrupt_json_returns_empty(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert dedup.load_sent_history(str(path)) == {}


def test_load_sent_history_reads_real_schema(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps({"somehandle": {"first_sent_date": "2026-09-01", "run": "run1"}}),
        encoding="utf-8",
    )
    history = dedup.load_sent_history(str(path))
    assert history == {"somehandle": {"first_sent_date": "2026-09-01", "run": "run1"}}


# ---------------------------------------------------------------------------
# filter_already_sent -- DoD case 7
# ---------------------------------------------------------------------------


def test_filter_already_sent_excludes_known_handle():
    """A handle already present in history -> excluded even though it would
    pass every other filter/score."""
    history = {"knownhandle": {"first_sent_date": "2026-09-01", "run": "run1"}}
    records = [_record("knownhandle"), _record("newhandle")]
    survivors = dedup.filter_already_sent(records, history)
    assert [r["handle"] for r in survivors] == ["newhandle"]


def test_filter_already_sent_missing_or_unavailable_handle_not_excluded():
    history = {"somehandle": {"first_sent_date": "2026-09-01", "run": "run1"}}
    records = [{"follower_count": 100}, {"handle": "unavailable", "follower_count": 50}]
    survivors = dedup.filter_already_sent(records, history)
    assert len(survivors) == 2


def test_filter_already_sent_empty_history_excludes_nothing():
    records = [_record("a"), _record("b")]
    survivors = dedup.filter_already_sent(records, {})
    assert survivors == records


# ---------------------------------------------------------------------------
# update_sent_history -- DoD case 8
# ---------------------------------------------------------------------------


def test_update_sent_history_adds_new_and_writes_file(tmp_path):
    """A newly-sent record is merged into history and persisted to disk."""
    path = str(tmp_path / "data" / "sent_accounts_history.json")
    merged = dedup.update_sent_history({}, [_record("brandnew")], "run1", date(2026, 10, 1), path=path)

    assert merged["brandnew"] == {"first_sent_date": "2026-10-01", "run": "run1"}
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == merged


def test_update_sent_history_never_overwrites_first_sent(tmp_path):
    history = {"oldhandle": {"first_sent_date": "2026-01-01", "run": "run1"}}
    path = str(tmp_path / "data" / "sent_accounts_history.json")
    merged = dedup.update_sent_history(history, [_record("oldhandle")], "run2", date(2026, 10, 1), path=path)
    assert merged["oldhandle"] == {"first_sent_date": "2026-01-01", "run": "run1"}


def test_update_sent_history_skips_missing_or_unavailable_handles(tmp_path):
    path = str(tmp_path / "data" / "sent_accounts_history.json")
    merged = dedup.update_sent_history(
        {}, [{"handle": "unavailable"}, {"follower_count": 1}], "run1", date(2026, 10, 1), path=path
    )
    assert merged == {}


def test_update_sent_history_does_not_mutate_input_history(tmp_path):
    history = {"existing": {"first_sent_date": "2026-01-01", "run": "run1"}}
    path = str(tmp_path / "data" / "sent_accounts_history.json")
    dedup.update_sent_history(history, [_record("new1")], "run1", date(2026, 10, 1), path=path)
    assert "new1" not in history  # original dict passed in is untouched


def test_update_sent_history_write_failure_does_not_raise(monkeypatch, tmp_path):
    """A disk/permission failure on write is logged, never raised -- the
    merged dict is still returned (failure-isolation convention)."""

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(dedup.os, "makedirs", boom)
    path = str(tmp_path / "data" / "h.json")
    merged = dedup.update_sent_history({}, [_record("x")], "run1", date(2026, 10, 1), path=path)
    assert merged["x"]["run"] == "run1"
    assert not os.path.exists(path)
