# Architecture — Social Nexa Agent

> STATUS: DRAFT — needs human review before build starts.

## 1. High-level flow

```
cron (1PM / 5PM IST)
      │
      ▼
 orchestrator.py
      │
      ├─▶ session_manager.py  (Module 1) — load/validate saved IG session
      │        │ halt+notify on expiry/challenge
      │        ▼
      ├─▶ discovery.py        (Module 2) — city+niche+radius → candidate list
      │        ▼
      ├─▶ collector.py        (Module 3) — candidate handle → raw profile data
      │        ▼
      ├─▶ analyzer.py         (Module 4) — raw data → scored record (pure, no I/O)
      │        ▼
      ├─▶ storage.py          (Module 5) — scored records → timestamped CSV
      ├─▶ notifier.py         (Module 5) — Telegram summary message
      │
      └─▶ scheduler_rules.py  (Module 6) — window/min-max logic, called by
               orchestrator before/after the main pipeline to decide whether
               to run again or alert on unmet minimums
```

## 2. Module boundaries

| Module | File(s) | Owns | Does NOT own |
|---|---|---|---|
| 1 | `session_manager.py` | IG login/session persistence, expiry/challenge detection | scraping business data itself |
| 2 | `discovery.py` | Turning (city, niches, radius) into a deduped candidate list (name + handle guess + location) | Instagram data collection |
| 3 | `collector.py` | Pulling profile/post/highlight data for one handle via the session from Module 1 | Scoring, discovery |
| 4 | `analyzer.py` | Pure scoring function per `Scoring-Spec.md` | Any network/file I/O |
| 5 | `storage.py`, `notifier.py` | CSV output, Telegram message | Scoring logic |
| 6 | `scheduler_rules.py` + cron config | Window timing, min/max enforcement, alerting | Business logic of discovery/collection |

`orchestrator.py` is the only module allowed to import and sequence all
others; individual modules never import each other except analyzer/storage
consuming plain data structures (dicts) — no module reaches into another's
internals.

## 3. Data contracts between modules

- Module 2 → 3: list of `{name, handle_guess, address, lat, lon, niche,
  chain_group_id | null}`.
- Module 3 → 4: `{handle, bio, follower_count, following_count, posts: [...],
  highlight_count, video_count, image_count, ...}` with `"unavailable"` for
  any missing field (never `0`/`null` for "couldn't fetch").
- Module 4 → 5: input record + `{category_scores: {...}, total_score,
  flags: [...]}`.
- Module 5 output: one CSV row per candidate; Telegram message is a summary
  string, not structured data.

## 4. Persistence & secrets

- Instagram session file: local file on the VM, outside version control,
  path configurable via env var.
- Telegram bot token + chat ID: env vars / local `.env`, not committed.
- CSV outputs: local `output/` directory, timestamped filenames, retained
  indefinitely (no auto-deletion) unless disk constraints say otherwise.

## 5. Deployment

> CHANGED 2026-09-19: deployment target is now **GitHub Actions on a public
> repository**, superseding the original VM+cron plan. See
> `.github/workflows/daily-runs.yml` and README.md for the operational
> details (secrets setup, session refresh process, the 60-day repo
> inactivity auto-disable rule).

- GitHub Actions scheduled workflow (`on: schedule`, cron in UTC) triggers
  three times daily (Run 1 start, Run 2 start, 8PM IST email report),
  converted from IST to UTC explicitly and documented in the workflow YAML.
- Each run is a fresh, stateless runner: the Instagram session is restored
  at the start of every run from the `IG_SESSION_B64` repository secret
  (base64-encoded session file), never committed to the repo.
- `GMAIL_APP_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` are GitHub
  Actions repository secrets, referenced in the workflow as
  `${{ secrets.NAME }}`, never hardcoded or committed.
- Per-run CSV output is uploaded as a workflow artifact for that run
  (chosen over committing full CSVs back to the repo, to keep the public
  repo's git history free of scraped business data and avoid the extra
  storage.py rework a repo-commit path would need).
- A public repository has no VM to fall behind on OS/timezone config, but
  GitHub auto-disables a scheduled workflow after 60 days with no repo
  activity. The workflow mitigates this itself with a trivial bot-identity
  commit each run (updating a `last_run.txt` timestamp only — never lead
  data), which is real repo activity without publishing scraped business
  data in git history. README.md documents this.

## 6. Failure isolation

Every external call (Nominatim/Maps request, Instagram request, Telegram
send, file write) is wrapped at its call site in the module that owns it;
failures are caught, logged with context, and surfaced as a per-candidate
or per-run flag rather than propagating an unhandled exception up to
`orchestrator.py`. `orchestrator.py` itself wraps each module call so one
module's total failure (e.g. discovery returns nothing) still allows
downstream modules to run with an empty/partial input and report that
clearly instead of crashing.
