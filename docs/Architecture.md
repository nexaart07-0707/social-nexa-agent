# Architecture — Social Nexa Agent

> STATUS: DRAFT — needs human review before build starts.

## 1. High-level flow

> UPDATED 2026: Instagram login and Telegram were both removed (see PRD.md
> §3/§6) — `session_manager.py` no longer exists; collection is always
> anonymous/unauthenticated via `collector.get_anonymous_loader()`. Email
> (Gmail SMTP, via `notifier.py`) is now the sole notification channel.
> Deployment is GitHub Actions (section 5 below), not cron on a VM.

```
GitHub Actions trigger (1PM / 5PM / 8PM IST, see section 5)
      │
      ▼
 orchestrator.py
      │
      ├─▶ discovery.py        (Module 2) — city+niche+radius → candidate list
      │        ▼
      ├─▶ collector.py        (Module 3) — candidate handle → raw profile data,
      │        │                via an anonymous (logged-out) Instagram loader
      │        ▼
      ├─▶ filters.py + dedup.py (pre-scoring filter chain) — brand blocklist →
      │        already-sent dedup → follower cap → posting frequency; a
      │        (candidate, record) pair failing any rule is fully excluded,
      │        never reaching scoring/CSV/email
      │        ▼
      ├─▶ analyzer.py         (Module 4) — raw data → scored record (pure, no I/O)
      │        ▼
      ├─▶ storage.py          (Module 5) — scored records → timestamped CSV
      ├─▶ notifier.py         (Module 5) — email report (Gmail SMTP)
      │
      └─▶ scheduler_rules.py  (Module 6) — window/min-max logic, called by
               orchestrator before/after the main pipeline to decide whether
               to run again or alert on unmet minimums
```

## 2. Module boundaries

| Module | File(s) | Owns | Does NOT own |
|---|---|---|---|
| 1 | *(removed)* | Instagram login/session persistence was Module 1's job; removed entirely — collection is now always anonymous via `collector.get_anonymous_loader()`, no session/login/expiry handling anywhere | — |
| 2 | `discovery.py` | Turning (city, niches, radius) into a deduped candidate list (name + handle guess + location) | Instagram data collection |
| 3 | `collector.py` | Pulling profile/post/highlight data for one handle via an anonymous Instagram loader | Scoring, discovery, login/session state |
| — | `filters.py` | Pure, stateless pre-scoring exclusion rules: follower cap, posting frequency, brand blocklist (reads `data/brand_blocklist.json`) | Scoring, any cross-run persisted state, network I/O |
| — | `dedup.py` | The one pre-scoring rule with cross-run persisted state: load/filter/update against `data/sent_accounts_history.json` | Scoring, git commit/push (orchestrator's workflow YAML does that) |
| 4 | `analyzer.py` | Pure scoring function per `Scoring-Spec.md` | Any network/file I/O |
| 5 | `storage.py`, `notifier.py` | CSV output, email report (Gmail SMTP) — the sole notification channel | Scoring logic |
| 6 | `scheduler_rules.py` + GitHub Actions workflow | Window timing, min/max enforcement, alerting | Business logic of discovery/collection |

`orchestrator.py` is the only module allowed to import and sequence all
others; individual modules never import each other except analyzer/storage
consuming plain data structures (dicts) — no module reaches into another's
internals.

## 3. Data contracts between modules

- Module 2 → 3: list of `{name, handle_guess, address, lat, lon, niche,
  chain_group_id | null}`.
- Module 3 → filter chain → 4: the exact same record shape passes through
  `filters.py`/`dedup.py` unchanged (`{handle, bio, follower_count,
  following_count, posts: [...], highlight_count, video_count,
  image_count, ...}`, `"unavailable"` for any missing field, never
  `0`/`null`) — the filter chain is a pure boolean gate on this shape, not
  a transformation of it. Only surviving records reach Module 4.
- Module 4 → 5: input record + `{category_scores: {...}, total_score,
  flags: [...]}`.
- Module 5 output: one CSV row per candidate; the email report is a
  formatted HTML view of that same data, not a separate data source.

## 4. Persistence & secrets

- No Instagram session/login exists — collection is always anonymous.
- Gmail App Password + sender/recipient: GitHub Actions repository secrets
  (`GMAIL_APP_PASSWORD`, `GMAIL_SENDER_EMAIL`, `GMAIL_RECIPIENT_EMAIL`), not
  committed.
- CSV outputs: local `output/` directory, timestamped filenames; uploaded as
  a per-run GitHub Actions artifact (see section 5) rather than committed.
- `data/brand_blocklist.json` (Rule 3) and `data/sent_accounts_history.json`
  (Rule 4) ARE committed to the repo — the former is a manually-curated
  config file, the latter is persisted cross-run state updated by the
  workflow's bot-identity commit (same pattern as `last_run.txt`).

## 5. Deployment

> CHANGED 2026-09-19: deployment target is now **GitHub Actions on a public
> repository**, superseding the original VM+cron plan. CHANGED again
> shortly after: Instagram login and Telegram were both removed entirely
> (collection is anonymous; email is the sole channel) — see
> `.github/workflows/daily-runs.yml` and README.md for current operational
> details (secrets setup, the 60-day repo inactivity auto-disable rule).

- GitHub Actions scheduled workflow (`on: schedule`, cron in UTC) triggers
  three times daily (Run 1 start, Run 2 start, 8PM IST email report),
  converted from IST to UTC explicitly and documented in the workflow YAML.
- Each run is a fresh, stateless runner with no session to restore —
  collection uses a plain anonymous Instagram loader, no login/secret
  needed for that part at all.
- `GMAIL_APP_PASSWORD` (required), `GMAIL_SENDER_EMAIL`/
  `GMAIL_RECIPIENT_EMAIL` (optional) are GitHub Actions repository secrets,
  referenced in the workflow as `${{ secrets.NAME }}`, never hardcoded or
  committed.
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

Every external call (Nominatim/Maps request, Instagram request, email
send, file write) is wrapped at its call site in the module that owns it;
failures are caught, logged with context, and surfaced as a per-candidate
or per-run flag rather than propagating an unhandled exception up to
`orchestrator.py`. `orchestrator.py` itself wraps each module call so one
module's total failure (e.g. discovery returns nothing) still allows
downstream modules to run with an empty/partial input and report that
clearly instead of crashing.
