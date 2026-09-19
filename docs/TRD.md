# TRD — Social Nexa Agent

> STATUS: DRAFT — needs human review before build starts.

## 1. Tech Stack (all free, zero paid tier)

- Language: Python 3.11+
- Instagram access: `instaloader` (documented session-file reuse pattern —
  login once interactively, reuse `.session` file every run)
- Discovery: `requests` against OpenStreetMap Nominatim (free, rate-limited
  public API) as primary source; optional public Google Maps page scraping
  as a fallback (no paid Places API)
- Storage: local CSV (stdlib `csv`), timestamped filenames; optional Google
  Sheets export via free `gspread` + a free Google service account if
  desired later — CSV is the baseline deliverable
- Notification: Telegram Bot API (free) via `requests`, using a bot token
  the operator creates via @BotFather
- Scheduling: `cron` on a free-tier VM (e.g. Oracle Cloud Always Free)
- Testing: `pytest`, stdlib `unittest.mock` for network calls

## 2. Module Definitions of Done

### Module 1 — Session Manager (Instagram)
**Build:** One-time interactive login via instaloader; session saved to a
local file; every subsequent run loads that file instead of prompting for
credentials. Detect two failure states and halt without retrying
automation: (a) saved session is rejected/expired, (b) Instagram returns a
checkpoint/verification challenge. Both cases: log clearly, send a Telegram
notification asking the human to re-run the login step, and exit non-zero.
**DoD:** Running the script twice shows the second run reusing the saved
session with no re-login prompt. Verified by manager via two consecutive
real runs.

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
(`leads_YYYY-MM-DD_HHMM.csv`). Sends a Telegram message on run completion
containing: run summary (candidates found/scored/flagged), a reference to
the output file, and any errors encountered during the run.
**DoD:** A test run produces a CSV with the documented column set (see
Scoring-Spec.md output contract) that opens correctly, AND a real Telegram
message is received by the operator's bot chat. Verified by manager
inspecting the CSV and confirming message receipt.

### Module 6 — Scheduler
**Build:** Cron entries for 1:00 PM and 5:00 PM IST — the VM's local
timezone is explicitly converted/verified against IST (do not assume
server TZ = IST). Enforces per-window minimum/maximum qualifying-result
counts from PRD.md §5 (Run 1: min 2; Run 2: min 2 / max 3). If a window's
minimum isn't met by its cutoff time, logs it and sends a Telegram alert
rather than failing silently.
**DoD:** A dry-run simulation (feeding synthetic clock times/results, not
waiting on the real clock) proves: (a) each window's start/stop logic
triggers at the correct IST-equivalent server time, (b) min/max enforcement
correctly accepts/rejects result counts at each boundary. Verified by
manager running the simulation and checking assertions.

## 3. Cross-cutting requirements

- Every external call (network request, file I/O, scrape) wrapped in
  try/except at the call site; a single failure is logged with context and
  the run continues — never an unhandled crash of the whole pipeline.
- No secrets (Instagram session file, Telegram bot token) committed to
  version control — `.gitignore` covers session files, `.env`, and any
  token config.
- All "unavailable" / "needs manual review" markers must propagate
  end-to-end into the final CSV and the Telegram summary, never get
  silently dropped.

## 4. Testing approach

Each module ships with basic automated tests (`pytest`) covering its DoD
condition, run by the worker while building and re-run by the manager
before sign-off. Modules 2 and 3 additionally require a live run against
real data (network mocking alone is not sufficient for their DoD, since the
DoD explicitly requires real profiles/real city).
