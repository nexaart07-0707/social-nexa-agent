"""
orchestrator.py — Integration layer (glue code only).

Wires together Modules 1-6 per Architecture.md section 1 ("High-level flow")
and enforces the failure-isolation rules from Architecture.md section 6.

`orchestrator.py` is the only file allowed to import and sequence all other
modules (Architecture.md section 2). It does not reimplement any module's
logic — it only calls the real public functions and handles the glue
(looping, try/except boundaries, wiring one module's output into the next
module's input).

--------------------------------------------------------------------------
KNOWN LIMITATION carried forward from Module 2 / Module 3's own reports
(documented per this build's instructions, not fixed here — out of scope):

    discovery.py produces `handle_guess` (a best-effort slug derived from
    the business name), NOT a confirmed, verified Instagram handle.
    collector.py's documented contract expects an already-resolved handle.
    No handle-resolution step (e.g. searching Instagram for the real
    account) has been built anywhere in this project. This orchestrator
    passes `handle_guess` straight into collector.collect_many() as if it
    were a confirmed handle. In real operation this means a meaningful
    fraction of "collected" records will actually be for nonexistent or
    wrong accounts (collector.py already handles that gracefully — a bad
    handle just produces an all-"unavailable" record via
    collector._empty_result(), it does not crash), and the resulting score
    should be read as reflecting the *guessed* handle, not a verified one.
    Fixing this would mean building a real IG-search-based resolver, which
    is explicitly out of scope for this integration pass.
--------------------------------------------------------------------------

FAILED-RECORD HANDLING design decision (also ambiguous in the brief):
    Each collected record is scored individually inside its own try/except.
    If analyzer.analyze() raises for a given record (it is a pure function
    and should not normally raise on collector.py's documented shape, but a
    malformed/unexpected record must never take down the whole run), the
    error is logged, the candidate/handle is recorded in the run's
    `errors` list, and that record is SKIPPED from the CSV rather than
    force-fed through analyze() a second time with synthetic data. This
    keeps the CSV honest (only genuinely-scored rows appear in it) while
    still surfacing the failure count in both the summary dict and the
    Telegram notification (`errors` list -> notify_run_complete's error
    count).
--------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Any

import analyzer
import collector
import discovery
import notifier
import scheduler_rules
import session_manager
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] orchestrator: %(message)s",
)
logger = logging.getLogger("orchestrator")

# The six confirmed niches (PRD.md section 5). Sourced from analyzer.py's
# NICHE_KEYS, which is the canonical list already shared with
# discovery.py's NICHE_OSM_TAGS keys (both enumerate the same six niches).
ALL_SIX_NICHES: tuple[str, ...] = analyzer.NICHE_KEYS


def _determine_run(run_override: str | None) -> str | None:
    """CLI/function override wins; otherwise auto-detect from current IST time."""
    if run_override:
        if run_override not in ("run1", "run2"):
            raise ValueError(f"--run must be 'run1' or 'run2', got {run_override!r}")
        logger.info("Run window forced via override: %s", run_override)
        return run_override

    window = scheduler_rules.get_window_for_time(scheduler_rules.now_ist())
    if window is None:
        logger.info("Current IST time is outside both run windows; nothing to do.")
        return None
    logger.info("Auto-detected run window from current IST time: %s", window)
    return window


def _halt_run(message: str) -> dict:
    """Session-failure path: per Module 1's own DoD this is a WHOLE-RUN halt,
    not a per-step continue. No downstream module is called at all."""
    logger.error("HALTING RUN: %s", message)
    try:
        notifier.notify_run_complete(csv_path=None, records=[], errors=[message])
    except Exception:  # noqa: BLE001 - notification must never mask the halt
        logger.exception("notify_run_complete raised while reporting the halt.")
    return {
        "candidates_found": 0,
        "records_collected": 0,
        "records_scored": 0,
        "csv_path": None,
        "errors": [message],
    }


def run(run_name: str | None = None, dry_run: bool = False) -> dict:
    """
    Execute one full orchestrator pass.

    Args:
        run_name: "run1" / "run2" to force a specific window (testing/dry-run),
            or None to auto-detect from the current IST time via
            scheduler_rules.
        dry_run: if True, fakes every external call (session, discovery,
            collection, Telegram) with synthetic in-memory data so the full
            pipeline can be exercised with zero real network/Instagram/
            Telegram calls. The CSV is still written for real (cheap, local,
            safe) so the pipeline's actual output shape is proven end to end.

    Returns:
        Summary dict: candidates_found, records_collected, records_scored,
        csv_path, errors.
    """
    errors: list[Any] = []

    # ---- Step 1: determine the run window ---------------------------------
    if dry_run and run_name is None:
        # --dry-run's whole purpose is to exercise every step of the pipeline
        # on demand (Workflow.md's "manager runs one full end-to-end dry
        # test"), regardless of what time it happens to be invoked. Without
        # this, a dry run launched outside both real windows would legitimately
        # (and correctly, per step 1's own contract) exit before touching any
        # other step -- which would prove nothing about steps 2-7. Default to
        # run1 so `--dry-run` alone is always a full pipeline exercise; an
        # explicit `--run run2 --dry-run` still targets run2 as asked.
        logger.info("dry-run with no --run override: defaulting to run1 so the full pipeline runs.")
        run_name = "run1"
    determined_run = _determine_run(run_name)
    if determined_run is None:
        print("[orchestrator] No active run window and no --run override; exiting cleanly.")
        return {
            "candidates_found": 0,
            "records_collected": 0,
            "records_scored": 0,
            "csv_path": None,
            "errors": [],
        }
    print(f"[orchestrator] Step 1/7: run window = {determined_run}")

    # ---- Step 2: ensure a ready Instagram session --------------------------
    if dry_run:
        print("[orchestrator] Step 2/7: session_manager.ensure_session() -- FAKED (dry-run), "
              "no real Instagram login/session check performed.")
        loader = _dry_run_fake_loader()
    else:
        try:
            session_manager.ensure_session()
        except (
            session_manager.SessionInvalidError,
            session_manager.ChallengeRequiredError,
            session_manager.SessionManagerError,
        ) as exc:
            # Deliberate exception to the general "log and continue" rule:
            # per Module 1's own DoD, a session failure halts the WHOLE run
            # (nothing downstream can work without a session), rather than
            # being isolated to "this step failed, continue with a gap."
            return _halt_run(f"Session unavailable, halting run per Module 1 DoD: {exc}")
        loader = session_manager.get_loader()
        print("[orchestrator] Step 2/7: session ready.")

    # ---- Step 3: discovery --------------------------------------------------
    candidates: list[dict] = []
    if dry_run:
        candidates = _dry_run_fake_candidates(determined_run)
        print(f"[orchestrator] Step 3/7: discovery.discover_for_run() -- FAKED (dry-run), "
              f"{len(candidates)} synthetic candidate(s).")
    else:
        try:
            candidates = discovery.discover_for_run(determined_run, niches=list(ALL_SIX_NICHES))
        except Exception as exc:  # noqa: BLE001 - failure isolation, per Architecture.md §6
            logger.exception("discovery.discover_for_run failed; continuing with zero candidates.")
            errors.append(f"discovery failed: {exc}")
            candidates = []
        print(f"[orchestrator] Step 3/7: discovery found {len(candidates)} candidate(s).")

    # ---- Step 4: collection ---------------------------------------------------
    # KNOWN LIMITATION (see module docstring): handle_guess is used directly
    # as if it were a confirmed handle -- no resolver exists yet.
    handles = [c["handle_guess"] for c in candidates]
    records: list[dict] = []
    if candidates:
        if dry_run:
            records = _dry_run_fake_records(handles)
            print(f"[orchestrator] Step 4/7: collector.collect_many() -- FAKED (dry-run), "
                  f"{len(records)} synthetic record(s).")
        else:
            try:
                records = collector.collect_many(loader, handles)
            except Exception as exc:  # noqa: BLE001 - failure isolation, per Architecture.md §6
                logger.exception("collector.collect_many failed; continuing with zero records.")
                errors.append(f"collection failed: {exc}")
                records = []
            print(f"[orchestrator] Step 4/7: collected {len(records)} record(s).")
    else:
        print("[orchestrator] Step 4/7: skipped (no candidates to collect).")

    # ---- Step 5: analysis / scoring ---------------------------------------
    # candidates and records are index-aligned (collect_many preserves the
    # input handles list's order and length).
    scored_pairs: list[tuple[dict, dict]] = []
    analyzed_records: list[dict] = []
    for candidate, record in zip(candidates, records):
        try:
            analyzed = analyzer.analyze(record, candidate["niche"])
        except Exception as exc:  # noqa: BLE001 - one bad record must not stop the others
            logger.exception(
                "analyzer.analyze failed for handle=%r; skipping this record.",
                record.get("handle"),
            )
            errors.append(f"analysis failed for handle={record.get('handle')!r}: {exc}")
            continue
        scored_pairs.append((candidate, analyzed))
        analyzed_records.append(analyzed)
    print(f"[orchestrator] Step 5/7: scored {len(analyzed_records)} record(s) "
          f"({len(records) - len(analyzed_records)} skipped due to analysis errors).")

    # ---- Step 6: write CSV (always real, even in dry-run) ------------------
    csv_path: str | None = None
    try:
        csv_path = storage.write_leads_csv(scored_pairs)
        print(f"[orchestrator] Step 6/7: CSV written to {csv_path!r}.")
    except Exception as exc:  # noqa: BLE001 - failure isolation, per Architecture.md §6
        logger.exception("storage.write_leads_csv failed unexpectedly.")
        errors.append(f"csv write failed: {exc}")

    # ---- Step 7: Telegram completion notification --------------------------
    if dry_run:
        print("[orchestrator] Step 7/7: notifier.notify_run_complete() -- Telegram send FAKED "
              "(dry-run); notifier's own no-token-set skip path is exercised for real.")
    try:
        sent = notifier.notify_run_complete(csv_path, analyzed_records, errors)
        print(f"[orchestrator] Step 7/7: notify_run_complete() returned {sent} "
              f"(False is expected/normal when no Telegram credentials are configured).")
    except Exception as exc:  # noqa: BLE001 - failure isolation, per Architecture.md §6
        logger.exception("notifier.notify_run_complete failed unexpectedly.")
        errors.append(f"notification failed: {exc}")

    summary = {
        "candidates_found": len(candidates),
        "records_collected": len(records),
        "records_scored": len(analyzed_records),
        "csv_path": csv_path,
        "errors": errors,
    }
    print(f"[orchestrator] DONE. Summary: {summary}")
    return summary


# ---------------------------------------------------------------------------
# --dry-run synthetic data helpers
# ---------------------------------------------------------------------------
# These fake ONLY the external-service calls (Instagram session/login,
# Nominatim discovery, Instagram profile collection, Telegram send).
# storage.write_leads_csv() and analyzer.analyze() are the REAL functions,
# run for real against this synthetic data, so the CSV produced is a
# genuine, correctly-structured artifact -- not itself faked.


class _FakeLoaderSentinel:
    """Stand-in object passed where a real instaloader.Instaloader would go.
    Never touched by fake collection below, since collection is also faked."""


def _dry_run_fake_loader():
    return _FakeLoaderSentinel()


def _dry_run_fake_candidates(run_name: str) -> list[dict]:
    niches = list(ALL_SIX_NICHES)
    return [
        {
            "name": "Sunrise Cafe",
            "handle_guess": "sunrisecafe",
            "address": "MG Road, Hubli, Karnataka, India",
            "lat": 15.3647,
            "lon": 75.1240,
            "niche": niches[0],
            "chain_group_id": None,
        },
        {
            "name": "BrightMinds Tutorials",
            "handle_guess": "brightmindstutorials",
            "address": "Andheri West, Mumbai, Maharashtra, India",
            "lat": 19.1197,
            "lon": 72.8468,
            "niche": niches[1],
            "chain_group_id": None,
        },
        {
            "name": "Glow Salon Studio",
            "handle_guess": "glowsalonstudio",
            "address": "Koramangala, Bengaluru, Karnataka, India",
            "lat": 12.9352,
            "lon": 77.6146,
            "niche": niches[3],
            "chain_group_id": None,
        },
    ]


def _dry_run_fake_records(handles: list[str]) -> list[dict]:
    """One healthy synthetic record, one deliberately-broken/incomplete
    record (to exercise the collector "unavailable" pattern + the
    orchestrator's per-record error isolation), one fully "unavailable"
    record (simulating a bad/nonexistent handle_guess, i.e. the documented
    handle-resolution gap in action)."""
    now = datetime.now(timezone.utc)
    fake_records = []
    for i, handle in enumerate(handles):
        if i == 0:
            fake_records.append(
                {
                    "handle": handle,
                    "bio": "Fresh coffee & pastries daily. 📍 Hubli. DM to order! Open 8am-9pm.",
                    "follower_count": 842,
                    "following_count": 210,
                    "posts": [
                        {"date": now.isoformat(), "likes": 12, "comments": 2, "media_type": "image"},
                        {"date": now.isoformat(), "likes": 9, "comments": 1, "media_type": "video"},
                        {"date": now.isoformat(), "likes": 15, "comments": 3, "media_type": "carousel"},
                    ],
                    "highlight_count": 1,
                    "video_count": 1,
                    "image_count": 2,
                }
            )
        elif i == 1:
            # Deliberately malformed (wrong type for follower_count) to
            # exercise the per-record try/except in step 5 without crashing
            # the rest of the run.
            fake_records.append(
                {
                    "handle": handle,
                    "bio": "unavailable",
                    "follower_count": "not-a-number",  # malformed on purpose
                    "following_count": "unavailable",
                    "posts": "unavailable",
                    "highlight_count": "unavailable",
                    "video_count": "unavailable",
                    "image_count": "unavailable",
                }
            )
        else:
            # Simulates collector._empty_result(): a bad/nonexistent handle
            # (the documented handle_guess-as-handle limitation in action).
            fake_records.append(collector._empty_result(handle, error="simulated: handle not found"))
    return fake_records


# ---------------------------------------------------------------------------
# Daily email-report glue (8 PM IST trigger, GitHub Actions "report" step)
#
# Minimal glue only: finds today's already-written run1/run2 CSVs (written
# earlier the same day by the 1PM/5PM `run()` calls above) and hands them to
# notifier.send_daily_email_report(). Reuses notifier.py's own zero-results
# handling (build_report_html's "No records to report for this run." /
# _report_summary_line's "0 qualifying results.") rather than inventing new
# empty-state logic here -- a run whose CSV is missing/not found simply gets
# an empty records list, exactly like a run that genuinely found nothing.
# ---------------------------------------------------------------------------


def find_todays_run_csvs(
    output_dir: str = "output", report_date: date | None = None
) -> dict[str, str | None]:
    """
    Locate today's run1 and run2 CSVs among storage.write_leads_csv()'s
    `leads_YYYY-MM-DD_HHMM.csv` outputs.

    storage.py timestamps filenames with UTC wall-clock time (datetime.now
    (timezone.utc), see storage.write_leads_csv's default `now`). Run1/run2
    windows (1PM-5PM / 5PM-7PM IST) fall entirely within 07:30-14:30 UTC, so
    the UTC calendar date always matches the IST calendar date for these
    windows -- no cross-midnight edge case here. Each filename's embedded
    HHMM is parsed back into a UTC datetime and classified into run1/run2 via
    scheduler_rules.get_window_for_time() (the same window logic `run()`
    itself uses), rather than re-deriving the IST window boundaries here.

    Returns {"run1": path_or_None, "run2": path_or_None}. If a window has
    more than one CSV (e.g. a manual re-run), the lexicographically-last
    (i.e. latest HHMM) match wins.
    """
    report_date = report_date or scheduler_rules.now_ist().date()
    pattern = os.path.join(output_dir, f"leads_{report_date.isoformat()}_*.csv")

    found: dict[str, str | None] = {"run1": None, "run2": None}
    for path in sorted(glob.glob(pattern)):
        stem = os.path.basename(path)[len("leads_"):-len(".csv")]  # "YYYY-MM-DD_HHMM"
        hhmm = stem.split("_")[-1]
        try:
            hour, minute = int(hhmm[:2]), int(hhmm[2:])
        except (ValueError, IndexError):
            logger.warning("Skipping CSV with unparseable timestamp: %s", path)
            continue

        dt_utc = datetime(
            report_date.year, report_date.month, report_date.day,
            hour, minute, tzinfo=timezone.utc,
        )
        window = scheduler_rules.get_window_for_time(dt_utc)
        if window in found:
            found[window] = path  # sorted order -> last match = latest HHMM

    return found


def run_report(report_date: date | None = None, output_dir: str = "output") -> dict:
    """
    Execute the 8PM-IST daily-email-report step: for each of run1/run2, load
    that run's already-scored CSV (if found) via
    notifier.load_scored_records_from_csv() and send the report via
    notifier.send_daily_email_report() -- an empty list (and therefore
    notifier's own "0 qualifying results" wording) when no CSV is found for
    that window, never a fabricated/synthetic substitute.

    Returns {"run1": {...}, "run2": {...}}, each with csv_path, record_count,
    sent, and (only on a load failure) an "error" key.
    """
    report_date = report_date or scheduler_rules.now_ist().date()
    csvs = find_todays_run_csvs(output_dir=output_dir, report_date=report_date)

    results: dict[str, dict] = {}
    for run_name in ("run1", "run2"):
        path = csvs.get(run_name)
        records: list[dict] = []
        error: str | None = None

        if path:
            try:
                records = notifier.load_scored_records_from_csv(path)
            except Exception as exc:  # noqa: BLE001 - failure isolation, per Architecture.md §6
                logger.exception("Failed to load CSV %s for %s report; reporting as empty.", path, run_name)
                error = f"failed to load {path}: {exc}"
                records = []
        else:
            logger.warning("No CSV found for %s on %s; reporting 0 qualifying results.", run_name, report_date)

        sent = notifier.send_daily_email_report(run_name, records, report_date)
        result = {"csv_path": path, "record_count": len(records), "sent": sent}
        if error:
            result["error"] = error
        results[run_name] = result
        print(f"[orchestrator] report: {run_name} -> csv={path!r} records={len(records)} sent={sent}")

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    parser = argparse.ArgumentParser(description="Social Nexa Agent orchestrator")
    parser.add_argument(
        "--run", choices=["run1", "run2"], default=None,
        help="Force a specific run window instead of auto-detecting from current IST time.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Exercise the full pipeline with synthetic data; no real network/Instagram/"
             "Telegram calls. Still writes a real CSV to output/.",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Run the 8PM-IST daily email report step instead of a research run: finds "
             "today's run1/run2 CSVs under output/ and emails the report for each "
             "(notifier.send_daily_email_report), reusing its own zero-results handling "
             "for any run whose CSV isn't found.",
    )
    args = parser.parse_args(argv)

    if args.report:
        results = run_report()
        print(f"[orchestrator] report DONE. {results}")
        return 0

    if args.dry_run:
        print("=" * 70)
        print("DRY RUN — no real Instagram/Nominatim/Telegram calls will be made.")
        print("=" * 70)

    summary = run(run_name=args.run, dry_run=args.dry_run)
    logger.debug("CLI run summary: %s", summary)

    # A run that never got started (outside both windows, no override) or a
    # halted run (session failure) both still exit 0 -- both are documented,
    # clean, non-crashing outcomes, not process failures.
    return 0


if __name__ == "__main__":
    sys.exit(main())
