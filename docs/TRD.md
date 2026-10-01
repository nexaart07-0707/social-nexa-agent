# TRD — Social Nexa Agent

> STATUS: DRAFT — needs human review before build starts.

## 1. Tech Stack (all free, zero paid tier)

> UPDATED: Instagram login and Telegram were both removed after the
> original build (see Module 1 below and PRD.md §6 constraint 3).
> Deployment moved from a VM+cron to GitHub Actions (PRD.md §6 constraint 4).

- Language: Python 3.11+
- Instagram access: `instaloader`, always anonymous/unauthenticated — no
  login, no session file, no credentials
- Discovery: `requests` against OpenStreetMap Nominatim (free, rate-limited
  public API) as primary source; optional public Google Maps page scraping
  as a fallback (no paid Places API)
- Storage: local CSV (stdlib `csv`), timestamped filenames, uploaded as a
  GitHub Actions artifact per run
- Notification: Gmail SMTP (free) via stdlib `smtplib`, using a Gmail App
  Password the operator generates (see README.md)
- Scheduling: GitHub Actions `on: schedule` (cron, UTC) on a public repo
- Testing: `pytest`, stdlib `unittest.mock` for network calls

## 2. Module Definitions of Done

### Module 1 — Session Manager (Instagram) — REMOVED
Originally: one-time interactive login via instaloader, session reused
across runs, graceful halt + Telegram notification on expiry/challenge.
**This module was removed entirely.** Collection is now always anonymous
(`collector.get_anonymous_loader()`) — no login, no session file, no
expiry/challenge handling. Tradeoff: more frequent `"unavailable"` fields
under Instagram's anonymous-access rate limiting (handled gracefully by
Module 3's existing never-crash design, not a new failure mode).

### Module 2 — Business Discovery
**Build:** Given city + niche list + radius, query Nominatim (and/or public
Maps scraping fallback) for candidate businesses. Normalize name +
address/location, dedupe (case-insensitive name match + geo-proximity
threshold for multi-branch chains — keep each branch as a separate record
but flag chain membership).
**DoD:** A real test city (from PRD.md §5 once filled in) returns a
non-empty, deduplicated candidate list with no two entries that are the
same physical branch. Verified by manager reading the actual output list.

### Module 3 — Data Collector
**Build:** For each candidate handle, use the persisted session to pull:
bio, follower count, following count, last 10–15 posts (timestamp, like
count, comment count, media type), Highlight count, video-vs-image ratio.
Any field the platform doesn't return or that is private/blocked is marked
literal string `"unavailable"` — never `0` or `null` silently. Requests are
paced with randomized delay + exponential backoff on rate-limit responses.
**DoD:** Tested against 3 real profiles — one normal public account, one
with very few posts, one nonexistent handle — all three complete without
an unhandled exception and produce correctly marked output. Verified by
manager running against those 3 real handles.

### Module 3.5 — Pre-Scoring Filters (`filters.py`, `dedup.py`)

**Build:** Four exclusion rules run between Module 3 (collection) and
Module 4 (scoring), each a pure function returning `True` if the record
SURVIVES (proceed) and `False` if EXCLUDED. A record failing any rule never
reaches `analyzer.analyze()`. Order (cheapest/no-data-needed first): brand
blocklist → already-sent dedup → follower cap → posting frequency.

`filters.py` (stateless, no network/file I/O beyond reading the local
blocklist JSON):

- `filter_by_follower_cap(record: dict, max_followers: int = FOLLOWER_CAP) -> bool`
  — `FOLLOWER_CAP = 15000`. Excludes only when `record["follower_count"]`
  is a real `int`/`float` strictly greater than `max_followers`. Boundary:
  exactly `15000` survives. `"unavailable"` or any non-numeric value always
  survives.
- `filter_by_posting_frequency(record: dict, min_posts: int = MIN_POSTS_TRAILING_30D, window_days: int = POSTING_WINDOW_DAYS, now: datetime | None = None) -> bool`
  — `MIN_POSTS_TRAILING_30D = 2`, `POSTING_WINDOW_DAYS = 30`. Counts
  `record["posts"]` entries with a parseable ISO `date` within
  `window_days` days of `now` (defaults to `datetime.now(timezone.utc)`,
  overridable for deterministic tests, same convention as
  `analyzer.analyze`'s `now` param). Excludes only when that count is
  strictly less than `min_posts`. Boundary: exactly `2` posts in the window
  survives. `posts == "unavailable"`, a non-list, or zero parseable dates
  all survive (insufficient data, not confirmed insufficient posting).
- `filter_by_brand_blocklist(record: dict, business_name: str | None = None, blocklist_path: str = DEFAULT_BLOCKLIST_PATH) -> bool`
  — loads `data/brand_blocklist.json` (a flat JSON array of brand name
  strings; a missing/corrupt file is treated as "nothing to exclude", not
  an error). Normalizes (lowercase, strip all non-alphanumeric characters)
  the handle, the optional `business_name`, and each blocklist entry, then
  excludes if a blocklist entry is a substring of the normalized handle OR
  business name. One-directional matching (brand-inside-name) relies on
  the seed list containing only genuine, distinctive brand names — never a
  bare generic category word. Deliberately does not attempt fuzzy
  matching; a brand not in the list is not caught.

`dedup.py` (Rule 4 — the one rule with cross-run persisted state, kept in
its own module per this task's explicit instruction):

- `load_sent_history(path: str = DEFAULT_HISTORY_PATH) -> dict[str, dict]`
  — reads `data/sent_accounts_history.json`; returns `{}` if the file is
  missing (first-ever run) or fails to parse (logged, never raised).
- `filter_already_sent(records: list[dict], history: dict[str, dict]) -> list[dict]`
  — returns the subset of `records` whose `handle` is not already a key in
  `history`. A missing/`"unavailable"` handle is never excluded.
- `update_sent_history(history: dict, newly_sent_records: list[dict], run_name: str, sent_date: date, path: str = DEFAULT_HISTORY_PATH) -> dict[str, dict]`
  — merges newly-sent handles into a copy of `history` (first-sent wins,
  never overwritten), writes it to `path` as pretty-printed, sorted-keys
  JSON (write failures logged via `try/except OSError`, never raised), and
  returns the merged dict. Called by `orchestrator.py` only after
  `notifier.send_daily_email_report()` returns `True` for that run.

**Data file schemas:**

- `data/brand_blocklist.json` — a flat JSON array of lowercase brand-name
  strings, e.g. `["starbucks", "mcdonalds", "dominos", ...]`. Manually
  curated; add a new string to the array to exclude another brand (see
  `README.md`).
- `data/sent_accounts_history.json` — a JSON object keyed by Instagram
  handle:
  ```json
  {
    "somehandle": { "first_sent_date": "2026-10-01", "run": "run1" }
  }
  ```
  Written by `dedup.update_sent_history()`; committed to the repo by
  `.github/workflows/daily-runs.yml`'s "Record last run timestamp" step
  (same bot-identity pattern as `last_run.txt`) — Python code never shells
  out to git itself.

**DoD:** Hand-crafted mock records covering each rule's exclude case, its
boundary (include) case, and its "data unavailable → survives" edge case,
plus an integration test proving an excluded candidate is fully absent from
both the written CSV and the built report HTML — not merely unscored. See
`test_filters.py`, `test_dedup.py`, and the filter-chain integration tests
in `test_orchestrator.py`.

### Module 4 — Rule-Based Analyzer
**Build:** Implements every rule in `Scoring-Spec.md` exactly — recency
bucket, posting consistency, engagement formula, bio keyword check, Reels
ratio, Highlights check, niche-specific checks, 0–2 score per category.
Pure function(s): input is a data record (as Module 3 would produce),
output is the scored record. No network calls in this module.
**DoD:** Run against 3 hand-crafted mock input records with known expected
score outputs (constructed from `Scoring-Spec.md`'s thresholds, including
at least one edge case per category boundary) — actual output matches
expected output exactly, field for field. Verified by manager running the
test suite and diffing output.

### Module 5 — Storage & Notification
**Build:** Writes all scored records for a run to a timestamped CSV
(`leads_YYYY-MM-DD_HHMM.csv`). Sends an email (Gmail SMTP) on run
completion containing the formatted report (now sent per-run — run1/run2
each email their own report immediately, plus an 8PM consolidated report —
see `notifier.py`/`orchestrator.py`).
**DoD:** A test run produces a CSV with the documented column set (see
Scoring-Spec.md output contract) that opens correctly, AND a real email is
received at the operator's configured address. Verified by manager
inspecting the CSV and confirming email receipt.

### Module 6 — Scheduler
**Build:** GitHub Actions `on: schedule` cron entries (UTC) for 1:00 PM /
5:00 PM / 8:00 PM IST — explicitly converted and documented in the workflow
YAML (IST = UTC+5:30, no DST). Enforces per-window minimum/maximum
qualifying-result counts from PRD.md §5 (Run 1: min 2; Run 2: min 2 / max 3).
If a window's minimum isn't met by its cutoff time, logs it rather than
failing silently (email is the only alert channel now — see Module 5).
**DoD:** A dry-run simulation (feeding synthetic clock times/results, not
waiting on the real clock) proves: (a) each window's start/stop logic
triggers at the correct IST-equivalent UTC cron time, (b) min/max
enforcement correctly accepts/rejects result counts at each boundary.
Verified by manager running the simulation and checking assertions.

## 3. Cross-cutting requirements

- Every external call (network request, file I/O, scrape) wrapped in
  try/except at the call site; a single failure is logged with context and
  the run continues — never an unhandled crash of the whole pipeline.
- No secrets (Gmail App Password) committed to version control —
  `.gitignore` covers `.env` and any local secret config; in CI, secrets
  live only in GitHub Actions repository secrets.
- All "unavailable" / "needs manual review" markers must propagate
  end-to-end into the final CSV and the email report, never get silently
  dropped.

## 4. Testing approach

Each module ships with basic automated tests (`pytest`) covering its DoD
condition, run by the worker while building and re-run by the manager
before sign-off. Modules 2 and 3 additionally require a live run against
real data (network mocking alone is not sufficient for their DoD, since the
DoD explicitly requires real profiles/real city).
