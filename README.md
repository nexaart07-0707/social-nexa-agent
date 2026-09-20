# Social Nexa Agent

Social Nexa Agent is an unattended lead-generation pipeline: twice a day it
discovers local businesses on Instagram in a target city/niche set (per
`docs/PRD.md`), collects their public profile/post data via
public/unauthenticated Instagram access (no login, no session), scores each
one against a fixed rule-based "opportunity score" (no AI/LLM judgment
anywhere in the scoring path), and emails the results to a human (Gmail —
the sole notification channel). Each run emails its own report immediately
on completion, and once a day it also emails a consolidated report of the
day's two runs. It now runs
entirely on **GitHub Actions against a public repository** — no VM, no
crontab — using GitHub's free scheduled-workflow triggers (see
`docs/Architecture.md` section 5 and `docs/PRD.md` constraint 4 for why this
superseded the original VM+cron plan).

## One-time setup

### a) Create the GitHub repo and push this code

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

The repo **must be public** — GitHub's unlimited free Actions minutes with
no card/cap requires a public repository (PRD.md constraint 4).

### b) Add repository secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add:

| Secret | Required? | Value |
|---|---|---|
| `GMAIL_APP_PASSWORD` | **Required** | A Gmail App Password (not your account password) — generate at https://myaccount.google.com/apppasswords after enabling 2-Step Verification |
| `GMAIL_SENDER_EMAIL` | Optional | defaults to the address baked into `notifier.py` if omitted |
| `GMAIL_RECIPIENT_EMAIL` | Optional | defaults the same way if omitted |

Never commit any of these values — the repo is public.

There is no Instagram login/session secret to set up: collection is
public/unauthenticated (see `collector.get_anonymous_loader()`).

## How the schedule works

`.github/workflows/daily-runs.yml` fires three `schedule` crons, always
interpreted by GitHub Actions in **UTC**. IST has no DST and is a fixed
UTC+5:30, so each is IST time minus 5:30:

| Trigger | IST time | UTC cron |
|---|---|---|
| Run 1 start | 1:00 PM IST | `30 7 * * *` |
| Run 2 start | 5:00 PM IST | `30 11 * * *` |
| Email report | 8:00 PM IST | `30 14 * * *` |

**Drift caveat:** GitHub Actions documents that scheduled workflows are not
guaranteed to fire at the exact minute — under platform load a run can be
delayed anywhere from a few minutes up to roughly an hour. A run firing
15–60 minutes late is expected platform behavior, not a bug in this
workflow or in `scheduler_rules.py`'s window logic.

## No session to manage

There is no login/session to refresh — collection is public/unauthenticated,
so there's nothing to expire and no credentials to rotate for Instagram
access. It IS still subject to Instagram's own anonymous rate-limiting,
which shows up as more `"unavailable"` fields in the output per this
project's existing data-quality design (see "Known limitations" below) —
that's expected behavior, not a workflow failure.

## The 60-day inactivity rule

GitHub auto-disables a scheduled workflow after 60 days with no repository
activity. The workflow mitigates this itself: its final step commits a
trivial `last_run.txt` timestamp (bot identity `github-actions[bot]`,
`[skip ci]`) on every invocation — **never** the CSV/lead data, which stays
out of the public repo's git history entirely (per Architecture.md §5) and
is instead uploaded as a per-run workflow artifact.

That commit step runs with `if: always()`, so it still executes even when an
earlier step fails and exits the job non-zero — a failing pipeline still
keeps the repo "active" and won't trip the 60-day auto-disable on its own.

The one scenario this doesn't cover: if the workflow stops running
*entirely* — the schedule itself gets disabled by a human, GitHub has an
outage, or the repo's default branch changes — no run ever fires to make
even that trivial commit. **A human should still check in on the repo
periodically regardless**, since no in-workflow mitigation can commit
anything if the workflow never runs at all.

## How to check logs

GitHub repo → **Actions** tab → select the `Daily Runs` workflow → click the
specific run → click the `run` job to see step-by-step logs (orchestrator
output, etc).

To download that run's CSV: same run page → **Artifacts** section at the
bottom → download `leads-csv-<run-id>` (zipped).

## How to manually trigger a run

GitHub repo → **Actions** tab → `Daily Runs` workflow → **Run workflow**
button → pick a `run_override`:

| `run_override` | Behavior |
|---|---|
| `auto` (default) | No real schedule context exists for a manual dispatch, so this defaults to testing `run1` |
| `run1` | Forces the Run 1 research pass (`orchestrator.py --run run1`) |
| `run2` | Forces the Run 2 research pass (`orchestrator.py --run run2`) |
| `report` | Forces the daily email report step (`orchestrator.py --report`), independent of whether run1/run2 ran today |

## Ponytail mode switching

This project's own build process used a manager/worker split with a
lite/ultra convention: `/ponytail lite` favors minimal, direct
implementations with no speculative abstraction (the mode this deployment
change was built under); `/ponytail ultra` is for when more thorough
exploration/robustness is warranted. Anyone continuing development on this
codebase should read `docs/Workflow.md` for the full manager/worker process
this project follows before making structural changes.

## Known limitations

- **`handle_guess` is not a verified Instagram handle.** `discovery.py`
  produces a best-effort slug from the business name; no handle-resolution
  step exists, so `orchestrator.py` passes it straight to `collector.py` as
  if confirmed. A meaningful fraction of "collected" records are for
  wrong/nonexistent accounts (handled gracefully as all-"unavailable", not a
  crash). See `orchestrator.py`'s module docstring.
- **Captions/`highlight_names` data gaps** between Module 3 (`collector.py`)
  and Module 4 (`analyzer.py`) — some fields collector.py doesn't populate
  that a more complete scoring pass could use. See each module's own notes.
- **Reels-ratio edge case** in the scoring logic — see `docs/Scoring-Spec.md`
  and `analyzer.py` for the specific boundary condition.
- **No Instagram login means more frequent `"unavailable"` fields.**
  Without an authenticated session, Instagram exposes less to logged-out
  requests — on borderline-private or rate-limited accounts, or certain post
  metadata, some fields may show `"unavailable"` more often than a
  logged-in session would see. This is expected, not a bug, per this
  project's existing rule to never treat `"unavailable"` as zero. See
  `collector.py`'s module docstring.
- **Real Gmail delivery is confirmed working** with human-provided
  credentials — already validated in an earlier build pass, not re-litigated
  here.
- **Real GitHub Actions end-to-end execution is PENDING.** Everything up to
  the point of actually creating the repo and secrets has been built and
  locally validated (workflow YAML syntax/logic, the `orchestrator.py
  --report` glue and its tests, full test suite). The worker that built this
  cannot create a GitHub repository, push code, add secrets, or trigger a
  real Actions run — that is explicitly the human's action to take next,
  exactly as the original Gmail credential setup was handled.
