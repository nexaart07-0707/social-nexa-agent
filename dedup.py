"""
dedup.py

Owns: Rule 4 -- persistent "already sent before" dedup against a local
history file (data/sent_accounts_history.json). Kept separate from
filters.py per the task's explicit instruction: filters.py owns the
stateless, per-run rules (follower cap, posting frequency, brand
blocklist); this module owns the one rule with cross-run persisted state.

Python here only ever WRITES the local JSON file -- the actual `git add` /
`git commit` / `git push` that carries it across GitHub Actions runs
happens in .github/workflows/daily-runs.yml's "Record last run timestamp"
step (same bot-identity commit pattern already used for last_run.txt).
This module (and orchestrator.py) never shells out to git itself, matching
this project's existing Python-does-data / YAML-does-git architecture
boundary.

CONCURRENCY NOTE: this project's actual run cadence is 3 scheduled times a
day, hours apart, each a full serial GitHub Actions job (`on: schedule`
gives no parallelism by design) -- so a simple read-then-write here is
sufficient. The only realistic race would come from two manually-triggered
overlapping `workflow_dispatch` runs; that scenario is caught by `git
push`'s own conflict rejection (one push wins, the other fails and must be
re-run), not silently corrupted data. Not solved further here since it is
not a realistic risk given this project's actual trigger pattern.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

UNAVAILABLE = "unavailable"

DEFAULT_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "data", "sent_accounts_history.json")


def load_sent_history(path: str = DEFAULT_HISTORY_PATH) -> dict[str, dict]:
    """
    Schema: {"<handle>": {"first_sent_date": "YYYY-MM-DD", "run": "run1"|"run2"}, ...}.
    Returns {} if the file doesn't exist (first-ever run) or on a JSON parse
    error (logged, never raised) -- never errors the caller.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load sent-accounts history %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def filter_already_sent(records: list[dict], history: dict[str, dict]) -> list[dict]:
    """
    Return the subset of `records` whose `handle` is NOT already a key in
    `history`. A record with a missing/"unavailable" handle is never
    excluded by this rule -- there's nothing to dedup against, so it's left
    to other filters.
    """
    survivors = []
    for record in records:
        handle = record.get("handle")
        if not isinstance(handle, str) or handle == UNAVAILABLE or handle not in history:
            survivors.append(record)
    return survivors


def update_sent_history(
    history: dict[str, dict],
    newly_sent_records: list[dict],
    run_name: str,
    sent_date: date,
    path: str = DEFAULT_HISTORY_PATH,
) -> dict[str, dict]:
    """
    Merge NEW handles from `newly_sent_records` into a COPY of `history`
    (first-sent wins -- an already-present handle's first_sent_date/run is
    never overwritten), write the merged result to `path` as pretty-printed,
    sorted-keys JSON, and return the merged dict.

    The file write is wrapped in try/except (OSError), per this project's
    existing failure-isolation convention: logged, never raised -- a write
    failure here must not take down the rest of the run.
    """
    merged = dict(history)
    for record in newly_sent_records:
        handle = record.get("handle")
        if not isinstance(handle, str) or handle == UNAVAILABLE:
            continue
        if handle in merged:
            continue  # first-sent wins
        merged[handle] = {"first_sent_date": sent_date.isoformat(), "run": run_name}

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, sort_keys=True)
    except OSError as exc:
        logger.error("Failed to write sent-accounts history to %s: %s", path, exc)

    return merged
